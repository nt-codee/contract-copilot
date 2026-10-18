import type { ReviewStage } from "./api";
import type { ApiErrorBody, ReviewResult } from "./types";

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export type ReviewSource = "fresh" | "cached" | "copied";
export interface ReviewResponse {
  review: ReviewResult;
  source?: ReviewSource;
}

const STAGES = new Set<ReviewStage>(["reading", "planning", "researching", "drafting", "verifying", "locating"]);
const invalid = () => new ApiError("invalid_response", "The review response could not be read. Please try again.");
const sourceOf = (value: unknown): ReviewSource | undefined =>
  value === "fresh" || value === "cached" || value === "copied" ? value : undefined;

function errorBody(value: unknown): ApiError {
  const error = (value as Partial<ApiErrorBody> | null)?.error;
  return new ApiError(
    typeof error?.code === "string" ? error.code : "unknown",
    typeof error?.message === "string" ? error.message : "Something went wrong on our side. Please try again.",
  );
}

/** Decode either a normal review response or the optional POST event stream. */
export async function readReviewResponse(response: Response, onProgress?: (stage: ReviewStage) => void): Promise<ReviewResponse> {
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw errorBody(body);
  }
  const source = sourceOf(response.headers.get("X-Review-Source"));
  if (response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "text/event-stream") {
    try {
      return { review: await response.json() as ReviewResult, source };
    } catch {
      throw invalid();
    }
  }
  if (!response.body) throw new ApiError("stream_incomplete", "The review connection ended before the result arrived. Please try again.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let result: ReviewResponse | undefined;
  let finished = false;

  function dispatch() {
    const name = event;
    const lines = data;
    event = "";
    data = [];
    // Comments, retry/id fields and unknown events (including heartbeats) carry no review state.
    if (lines.length === 0 || !["progress", "result", "error"].includes(name)) return;
    let value: unknown;
    try { value = JSON.parse(lines.join("\n")); } catch { throw invalid(); }
    if (name === "error") throw errorBody(value);
    if (!value || typeof value !== "object") throw invalid();
    if (name === "progress") {
      const stage = (value as { stage?: unknown }).stage;
      if (result || typeof stage !== "string" || !STAGES.has(stage as ReviewStage)) throw invalid();
      onProgress?.(stage as ReviewStage);
    } else {
      const payload = value as { review?: ReviewResult; source?: unknown };
      if (result || !payload.review || typeof payload.review !== "object" || typeof payload.review.id !== "string") throw invalid();
      const eventSource = sourceOf(payload.source);
      if (!eventSource) throw invalid();
      result = { review: payload.review, source: eventSource };
    }
  }

  function line(text: string) {
    if (text === "") return dispatch();
    if (text.startsWith(":")) return;
    const colon = text.indexOf(":");
    const field = colon < 0 ? text : text.slice(0, colon);
    let value = colon < 0 ? "" : text.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }

  function drain(final: boolean) {
    let start = 0;
    for (let i = 0; i < buffer.length; i++) {
      if (buffer[i] !== "\n" && buffer[i] !== "\r") continue;
      // A CR at a chunk boundary may be the first byte of CRLF.
      if (buffer[i] === "\r" && i === buffer.length - 1 && !final) break;
      line(buffer.slice(start, i));
      if (buffer[i] === "\r" && buffer[i + 1] === "\n") i++;
      start = i + 1;
    }
    buffer = buffer.slice(start);
  }

  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      drain(false);
    }
    buffer += decoder.decode();
    drain(true);
    // EOF is not an SSE event delimiter: an unframed terminal event is incomplete.
    if (!result || buffer || event || data.length) {
      throw new ApiError("stream_incomplete", "The review connection ended before the result was complete. Please try again.");
    }
    finished = true;
    return result;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("stream_interrupted", "The review connection was interrupted. Please try again.");
  } finally {
    if (!finished) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
