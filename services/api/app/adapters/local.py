"""Local adapters: disk blobs, JSON review store, in-memory cosine index, fake LLM and embedder.

Used by tests (CLOUD=local LLM=fake) and for offline development. No cloud SDK.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path

from app.config import EMBED_DIM, Settings
from app.models import CompanyDoc, ReviewResult
from app.ports import ChatMessage, Chunk, CompanyChunk, LLMUsage, PdfPart, T, ToolCall, ToolSpec

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeLLM:
    """Replays recorded JSON responses from fixtures/<purpose>.json. Records every call for tests."""

    model = "fake-llm"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = Path(fixtures_dir or os.environ.get("FAKE_FIXTURES_DIR") or FIXTURES_DIR)
        self.calls: list[dict] = []

    def chat(self, *, system: str, messages: list[ChatMessage], tools: list[ToolSpec],
             purpose: str, force_tools: bool = False) -> tuple[ChatMessage, LLMUsage]:
        """Replays fixtures/agent_turns.json: turn N answers the Nth time the model is asked."""
        self.calls.append({"purpose": purpose, "system": system, "parts": [], "messages": list(messages)})
        turns = json.loads((self.fixtures_dir / "agent_turns.json").read_text(encoding="utf-8"))["turns"]
        n = sum(1 for m in messages if m.role == "model")
        turn = turns[n] if n < len(turns) else {"text": "DONE", "tool_calls": []}
        reply = ChatMessage(role="model", text=turn.get("text", ""),
                            tool_calls=[ToolCall(name=c["name"], args=c["args"], id=f"call-{n}-{i}")
                                        for i, c in enumerate(turn.get("tool_calls", []))])
        return reply, LLMUsage(self.model, len(system) // 4, 20)

    def generate(self, *, system: str, parts: list[str | PdfPart], schema: type[T],
                 purpose: str) -> tuple[T, LLMUsage]:
        self.calls.append({"purpose": purpose, "system": system, "parts": parts})
        raw = (self.fixtures_dir / f"{purpose}.json").read_text(encoding="utf-8")
        text_in = sum(len(p) for p in parts if isinstance(p, str)) + len(system)
        return schema.model_validate_json(raw), LLMUsage(self.model, text_in // 4, len(raw) // 4)


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder: good enough for lexical search in tests."""

    model = "hash-embedder"
    dim = EMBED_DIM

    def embed(self, texts: list[str], *, query: bool = False) -> tuple[list[list[float]], int]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in re.findall(r"\w+", t.lower()):
                h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=4).digest(), "big")
                v[h % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out, sum(len(t) // 4 for t in texts)


class DiskBlobStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("invalid key")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryIndex:
    """In-memory cosine search, optionally mirrored to a JSONL file so it survives restarts."""

    def __init__(self, path: Path | None = None):
        self._chunks: dict[str, Chunk] = {}
        self._lock = threading.Lock()
        self._path = path
        if path and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    c = Chunk(**json.loads(line))
                    self._chunks[c.id] = c

    def upsert(self, chunks: list[Chunk]) -> None:
        with self._lock:
            for c in chunks:
                self._chunks[c.id] = c
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    for c in chunks:
                        fh.write(json.dumps(c.__dict__) + "\n")

    def query(self, vector: list[float], limit: int) -> list[tuple[Chunk, float]]:
        with self._lock:
            scored = [(c, _cosine(vector, c.embedding)) for c in self._chunks.values()]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]


class MemoryCompanyStore:
    """Company policy docs and their chunks, in memory (a restart forgets them; the PDFs stay in blobs)."""

    def __init__(self) -> None:
        self._docs: dict[str, CompanyDoc] = {}
        self._chunks: dict[str, list[CompanyChunk]] = {}
        self._lock = threading.Lock()

    def put(self, doc: CompanyDoc, chunks: list[CompanyChunk]) -> None:
        with self._lock:
            self._docs[doc.id] = doc
            self._chunks[doc.id] = list(chunks)

    def list(self) -> list[CompanyDoc]:
        with self._lock:
            return sorted(self._docs.values(), key=lambda d: d.created_at, reverse=True)

    def get(self, doc_id: str) -> CompanyDoc | None:
        with self._lock:
            return self._docs.get(doc_id)

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            self._chunks.pop(doc_id, None)
            return self._docs.pop(doc_id, None) is not None

    def search(self, vector: list[float], limit: int) -> list[tuple[CompanyChunk, float]]:
        with self._lock:
            scored = [(c, _cosine(vector, c.embedding)) for cs in self._chunks.values() for c in cs]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]


class JsonReviewStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, review: ReviewResult) -> None:
        (self.root / f"{review.id}.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")

    def get(self, review_id: str) -> ReviewResult | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", review_id):
            return None
        p = self.root / f"{review_id}.json"
        return ReviewResult.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None

    def list(self, limit: int = 50) -> list[ReviewResult]:
        items = [ReviewResult.model_validate_json(p.read_text(encoding="utf-8"))
                 for p in self.root.glob("*.json")]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items[:limit]

    def find_by_sample(self, sample_id: str) -> ReviewResult | None:
        for r in self.list(limit=10_000):
            if r.sample_id == sample_id:
                return r
        return None


def local_stores(settings: Settings):
    root = settings.data_dir
    return (DiskBlobStore(root / "blobs"), MemoryIndex(root / "chunks.jsonl"),
            JsonReviewStore(root / "reviews"), MemoryCompanyStore())
