"""Interfaces the core depends on. Adapters in app/adapters/ implement them; nothing here imports a cloud SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Protocol, TypeVar

from pydantic import BaseModel

from app.models import AuditEvent, CompanyDoc, Identity, Invite, Member, ReviewResult, User, Workspace

T = TypeVar("T", bound=BaseModel)


class UpstreamError(Exception):
    """A dependency (LLM, embeddings, cloud store) failed. The message is safe to show users.

    `status` is the upstream HTTP status when known; it is for logs only, never shown to users.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class InvalidToken(Exception):
    """The bearer token is missing its signature, expired, or not for this project. Safe to show."""


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
    owner: str = "demo"  # workspace; every query is filtered by it


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
    def query(self, vector: list[float], limit: int, *, owner: str) -> list[tuple[Chunk, float]]:
        """Nearest chunks of ONE workspace, with a similarity score in [-1, 1], higher is closer."""
        ...
    def delete_review(self, review_id: str, *, owner: str) -> None: ...
    def delete_owner(self, owner: str) -> None: ...


class ReviewStore(Protocol):
    """Every read is scoped by `owner`: a review in another workspace is simply not found."""

    def save(self, review: ReviewResult) -> None: ...
    def get(self, review_id: str, *, owner: str) -> ReviewResult | None: ...
    def list(self, *, owner: str, limit: int = 50) -> list[ReviewResult]:
        """Newest first."""
        ...
    def find_by_sample(self, sample_id: str, *, owner: str) -> ReviewResult | None: ...
    def runs_of(self, content_hash: str, *, owner: str) -> list[ReviewResult]:
        """Every run of this content in this workspace, newest first."""
        ...
    def delete(self, review_id: str, *, owner: str) -> bool: ...
    def delete_owner(self, owner: str) -> list[str]:
        """Remove a whole workspace. Returns the ids of the reviews removed."""
        ...
    def scan(self, *, limit: int, since: datetime | None = None, owner: str | None = None) -> Iterator[ReviewResult]:
        """Newest reviews, streamed: of one workspace, or (owner=None) of ALL. For aggregates only."""
        ...


@dataclass
class CompanyChunk:
    id: str
    doc_id: str
    title: str
    page: int
    text: str
    embedding: list[float] = field(default_factory=list)


class CompanyStore(Protocol):
    """The user's own policy documents: metadata plus embedded chunks for vector search.

    Every call is scoped by `owner`."""

    def put(self, doc: CompanyDoc, chunks: list[CompanyChunk], *, owner: str) -> None: ...
    def list(self, *, owner: str) -> list[CompanyDoc]:
        """Newest first."""
        ...
    def get(self, doc_id: str, *, owner: str) -> CompanyDoc | None: ...
    def delete(self, doc_id: str, *, owner: str) -> bool:
        """Remove the doc and its chunks. False when there was no such doc."""
        ...
    def search(self, vector: list[float], limit: int, *, owner: str) -> list[tuple[CompanyChunk, float]]: ...
    def delete_owner(self, owner: str) -> list[str]:
        """Remove every doc of a workspace. Returns the removed doc ids."""
        ...


class AuthVerifier(Protocol):
    def verify(self, token: str) -> Identity:
        """Return the identity the token proves, or raise InvalidToken."""
        ...


class UserStore(Protocol):
    def get(self, uid: str) -> User | None: ...
    def put(self, user: User) -> None: ...
    def list(self) -> list[User]: ...
    def delete(self, uid: str) -> None: ...


class WorkspaceStore(Protocol):
    """Company workspaces, their members and invites. Collections `workspaces`, `workspace_members`
    (document id `<workspace_id>_<uid>`) and `invites`."""

    def get(self, workspace_id: str) -> Workspace | None: ...
    def create(self, ws: Workspace) -> bool:
        """Create if absent. False when the id already exists (a concurrent first sign-in won)."""
        ...
    def put(self, ws: Workspace) -> None: ...
    def delete(self, workspace_id: str) -> None:
        """Remove the workspace, its members and its invites."""
        ...
    def find_by_domain(self, domain: str) -> Workspace | None: ...
    def list_all(self) -> list[Workspace]: ...
    def get_member(self, workspace_id: str, uid: str) -> Member | None: ...
    def put_member(self, workspace_id: str, member: Member) -> None: ...
    def delete_member(self, workspace_id: str, uid: str) -> None: ...
    def members(self, workspace_id: str) -> list[Member]:
        """Oldest first."""
        ...
    def memberships(self, uid: str) -> list[tuple[Workspace, Member]]:
        """Every workspace the user belongs to, oldest membership first."""
        ...
    def put_invite(self, invite: Invite) -> None: ...
    def get_invite(self, code: str) -> Invite | None: ...
    def delete_invite(self, code: str) -> None: ...


class AuditLog(Protocol):
    """Append-only. There is no update, and no delete except `delete_actor`, which exists only
    for a user's own 'delete my data'."""

    def append(self, event: AuditEvent) -> None: ...
    def list_for(self, workspace_id: str, limit: int, *, actor: str | None = None) -> list[AuditEvent]:
        """A workspace's events (only one actor's when `actor` is given), newest first."""
        ...
    def list_all(self, limit: int) -> list[AuditEvent]:
        """Newest first, every workspace."""
        ...
    def delete_actor(self, actor: str) -> int: ...
    def delete_workspace(self, workspace_id: str) -> int:
        """Only when the whole workspace is removed because its last member deleted their data."""
        ...
