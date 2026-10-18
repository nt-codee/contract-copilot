"""Interfaces the core depends on. Adapters in app/adapters/ implement them; nothing here imports a cloud SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.models import CompanyDoc, ReviewResult

T = TypeVar("T", bound=BaseModel)


class UpstreamError(Exception):
    """A dependency (LLM, embeddings, cloud store) failed. The message is safe to show users.

    `status` is the upstream HTTP status when known; it is for logs only, never shown to users.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PdfPart:
    """Binary PDF handed to the LLM (scanned-document path)."""

    data: bytes
    mime_type: str = "application/pdf"


@dataclass
class LLMUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "LLMUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class Chunk:
    id: str
    review_id: str
    filename: str
    page: int
    text: str
    clause_type: str | None = None
    finding_id: str | None = None
    embedding: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict
    id: str | None = None


@dataclass
class ToolResult:
    name: str
    result: dict
    id: str | None = None


@dataclass
class ChatMessage:
    """One turn of a tool-using conversation, in SDK-neutral form.

    role "user": `text`; role "model": `text` and/or `tool_calls`; role "tool": `tool_results`.
    `raw` is the adapter's own object for a model turn (kept so signatures round-trip); opaque here.
    """

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    raw: Any = None


class LLM(Protocol):
    model: str

    def chat(self, *, system: str, messages: list[ChatMessage], tools: list[ToolSpec],
             purpose: str, force_tools: bool = False) -> tuple[ChatMessage, LLMUsage]:
        """One model turn with function calling on. Returns the model's message (text and/or tool calls).

        `force_tools` makes the model answer with at least one tool call (Gemini mode ANY)."""
        ...

    def generate(self, *, system: str, parts: list[str | PdfPart], schema: type[T],
                 purpose: str) -> tuple[T, LLMUsage]:
        """Return a validated instance of `schema`. `purpose` names the call (review, transcribe, ...)."""
        ...


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str], *, query: bool = False) -> tuple[list[list[float]], int]:
        """Return one vector of length `dim` per text, and the input tokens billed (estimate)."""
        ...


class BlobStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...


class SearchIndex(Protocol):
    def upsert(self, chunks: list[Chunk]) -> None: ...
    def query(self, vector: list[float], limit: int) -> list[tuple[Chunk, float]]:
        """Nearest chunks with a similarity score in [-1, 1], higher is closer."""
        ...


class ReviewStore(Protocol):
    def save(self, review: ReviewResult) -> None: ...
    def get(self, review_id: str) -> ReviewResult | None: ...
    def list(self, limit: int = 50) -> list[ReviewResult]: ...
    def find_by_sample(self, sample_id: str) -> ReviewResult | None: ...


@dataclass
class CompanyChunk:
    id: str
    doc_id: str
    title: str
    page: int
    text: str
    embedding: list[float] = field(default_factory=list)


class CompanyStore(Protocol):
    """The user's own policy documents: metadata plus embedded chunks for vector search."""

    def put(self, doc: CompanyDoc, chunks: list[CompanyChunk]) -> None: ...
    def list(self) -> list[CompanyDoc]:
        """Newest first."""
        ...
    def get(self, doc_id: str) -> CompanyDoc | None: ...
    def delete(self, doc_id: str) -> bool:
        """Remove the doc and its chunks. False when there was no such doc."""
        ...
    def search(self, vector: list[float], limit: int) -> list[tuple[CompanyChunk, float]]: ...
