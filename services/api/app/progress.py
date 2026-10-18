"""Request-local phase callbacks and bounded SSE; no stored jobs or synthetic progress."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import Callable
from typing import Literal

from fastapi.responses import JSONResponse, StreamingResponse

from app.models import ReviewResult

Stage = Literal["reading", "planning", "researching", "drafting", "verifying", "locating"]
Progress = Callable[[Stage], None]
ReviewJob = Callable[[Progress], tuple[ReviewResult, str]]
ErrorHandler = Callable[[Exception], tuple[int, dict]]
QUEUE_SIZE = 32
HEARTBEAT_SECONDS = 15


def report(progress: Progress | None, stage: Stage) -> None:
    if progress is not None:
        progress(stage)


def wants_stream(accept: str) -> bool:
    for item in accept.lower().split(","):
        media, *params = item.split(";")
        if media.strip() == "text/event-stream":
            try:
                return all(float(p.strip()[2:]) > 0 for p in params if p.strip().startswith("q="))
            except ValueError:
                return False
    return False


def encode_event(event: str, data: dict) -> bytes:
    # JSON escapes embedded newlines; UTF-8 is decoded incrementally by the browser across chunks.
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class ReviewChannel:
    def __init__(self, job: ReviewJob, error: ErrorHandler):
        self.events: queue.Queue[tuple[str, dict, int]] = queue.Queue(maxsize=QUEUE_SIZE)
        self.closed = threading.Event()
        self.finished = threading.Event()
        self.loop = asyncio.get_running_loop()
        self.wakeup = asyncio.Event()
        self.thread = threading.Thread(target=self._run, args=(job, error), daemon=True,
                                       name="review-progress")

    def send(self, event: str, data: dict, status: int = 200) -> None:
        while not self.closed.is_set():
            try:
                self.events.put((event, data, status), timeout=.1)
            except queue.Full:
                continue
            try:
                self.loop.call_soon_threadsafe(self.wakeup.set)
            except RuntimeError:  # a disconnected request's event loop may already be gone
                self.close()
            return

    def _run(self, job: ReviewJob, error: ErrorHandler) -> None:
        try:
            result, source = job(lambda stage: self.send("progress", {"stage": stage}))
            self.send("result", {"review": result.model_dump(mode="json"), "source": source})
        except Exception as exc:
            status, body = error(exc)
            self.send("error", body, status)
        finally:
            self.finished.set()

    async def receive(self) -> tuple[str, dict, int] | None:
        while True:
            self.wakeup.clear()
            try:
                return self.events.get_nowait()
            except queue.Empty:
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    return None  # heartbeat only; never invent a stage

    def close(self) -> None:
        self.closed.set()  # releases any producer waiting on the bounded queue; never restarts work


class ReviewStreamResponse(StreamingResponse):
    def __init__(self, channel: ReviewChannel, first: tuple[str, dict, int]):
        self.channel = channel

        async def body():
            event = first
            try:
                while True:
                    if event is None:
                        yield b": keep-alive\n\n"
                    else:
                        name, data, _ = event
                        yield encode_event(name, data)
                        if name in ("result", "error"):
                            break
                    event = await channel.receive()
            finally:
                channel.close()

        super().__init__(body(), media_type="text/event-stream",
                         headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also covers disconnect before body iteration begins, or a failed ASGI send.
            self.channel.close()


async def stream_review(job: ReviewJob, error: ErrorHandler):
    channel = ReviewChannel(job, error)
    channel.thread.start()
    try:
        first = await channel.receive()
        while first is None:
            first = await channel.receive()
        name, data, status = first
        if name == "error":  # guard failures before the pipeline starts keep their HTTP status/body
            channel.close()
            return JSONResponse(data, status_code=status)
        if name == "result":  # cache and copy paths retain the existing JSON contract/header
            channel.close()
            return JSONResponse(data["review"], headers={"X-Review-Source": data["source"]})
        return ReviewStreamResponse(channel, first)
    except BaseException:
        channel.close()
        raise
