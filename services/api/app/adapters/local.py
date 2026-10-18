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
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.config import EMBED_DIM, Settings
from app.models import AuditEvent, CompanyDoc, Identity, Invite, Member, ReviewResult, User, Workspace
from app.ports import (ChatMessage, Chunk, CompanyChunk, InvalidToken, LLMUsage, PdfPart, T, ToolCall,
                       ToolSpec)

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

    def _rewrite(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(json.dumps(c.__dict__) + "\n" for c in self._chunks.values()),
                                  encoding="utf-8")

    def upsert(self, chunks: list[Chunk]) -> None:
        with self._lock:
            for c in chunks:
                self._chunks[c.id] = c
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    for c in chunks:
                        fh.write(json.dumps(c.__dict__) + "\n")

    def query(self, vector: list[float], limit: int, *, owner: str) -> list[tuple[Chunk, float]]:
        with self._lock:
            scored = [(c, _cosine(vector, c.embedding)) for c in self._chunks.values() if c.owner == owner]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]

    def delete_review(self, review_id: str, *, owner: str) -> None:
        with self._lock:
            self._chunks = {k: c for k, c in self._chunks.items()
                            if not (c.review_id == review_id and c.owner == owner)}
            self._rewrite()

    def delete_owner(self, owner: str) -> None:
        with self._lock:
            self._chunks = {k: c for k, c in self._chunks.items() if c.owner != owner}
            self._rewrite()


class MemoryCompanyStore:
    """Company policy docs and their chunks, in memory (a restart forgets them; the PDFs stay in blobs).

    Keyed by owner first, so one workspace can never see another's documents."""

    def __init__(self) -> None:
        self._docs: dict[str, dict[str, CompanyDoc]] = {}
        self._chunks: dict[str, dict[str, list[CompanyChunk]]] = {}
        self._lock = threading.Lock()

    def put(self, doc: CompanyDoc, chunks: list[CompanyChunk], *, owner: str) -> None:
        with self._lock:
            self._docs.setdefault(owner, {})[doc.id] = doc
            self._chunks.setdefault(owner, {})[doc.id] = list(chunks)

    def list(self, *, owner: str) -> list[CompanyDoc]:
        with self._lock:
            return sorted(self._docs.get(owner, {}).values(), key=lambda d: d.created_at, reverse=True)

    def get(self, doc_id: str, *, owner: str) -> CompanyDoc | None:
        with self._lock:
            return self._docs.get(owner, {}).get(doc_id)

    def delete(self, doc_id: str, *, owner: str) -> bool:
        with self._lock:
            self._chunks.get(owner, {}).pop(doc_id, None)
            return self._docs.get(owner, {}).pop(doc_id, None) is not None

    def search(self, vector: list[float], limit: int, *, owner: str,
               doc_ids: frozenset[str] | None = None) -> list[tuple[CompanyChunk, float]]:
        with self._lock:
            scored = [(c, _cosine(vector, c.embedding))
                      for did, cs in self._chunks.get(owner, {}).items() if doc_ids is None or did in doc_ids
                      for c in cs]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]

    def delete_owner(self, owner: str) -> list[str]:
        with self._lock:
            ids = list(self._docs.pop(owner, {}))
            self._chunks.pop(owner, None)
            return ids


class JsonReviewStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, review: ReviewResult) -> None:
        (self.root / f"{review.id}.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")

    def _all(self) -> list[ReviewResult]:
        items = [ReviewResult.model_validate_json(p.read_text(encoding="utf-8"))
                 for p in self.root.glob("*.json")]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items

    def get(self, review_id: str, *, owner: str) -> ReviewResult | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", review_id):
            return None
        p = self.root / f"{review_id}.json"
        if not p.is_file():
            return None
        r = ReviewResult.model_validate_json(p.read_text(encoding="utf-8"))
        return r if r.owner == owner else None

    def list(self, *, owner: str, limit: int = 50) -> list[ReviewResult]:
        return [r for r in self._all() if r.owner == owner][:limit]

    def find_by_sample(self, sample_id: str, *, owner: str) -> ReviewResult | None:
        return next((r for r in self._all() if r.owner == owner and r.sample_id == sample_id), None)

    def runs_of(self, content_hash: str, *, owner: str) -> list[ReviewResult]:
        return [r for r in self._all() if r.owner == owner and r.content_hash == content_hash]

    def delete(self, review_id: str, *, owner: str) -> bool:
        if self.get(review_id, owner=owner) is None:
            return False
        (self.root / f"{review_id}.json").unlink()
        return True

    def delete_owner(self, owner: str) -> list[str]:
        ids = [r.id for r in self._all() if r.owner == owner]
        for i in ids:
            (self.root / f"{i}.json").unlink()
        return ids

    def scan(self, *, limit: int, since: datetime | None = None, owner: str | None = None) -> Iterator[ReviewResult]:
        pool = [r for r in self._all() if owner is None or r.owner == owner]
        for r in pool[:limit]:
            if since is None or r.created_at >= since:
                yield r


class LocalTokenVerifier:
    """CLOUD=local only: `Bearer test:<uid>:<email>[:hd=<domain>][:unverified]`. Anything else is invalid.

    Never constructed when CLOUD=gcp, so this format cannot be used against a deployed service."""

    def verify(self, token: str) -> Identity:
        parts = token.split(":")
        if len(parts) < 3 or parts[0] != "test" or not parts[1] or "@" not in parts[2]:
            raise InvalidToken("not a test token")
        flags = parts[3:]
        hd = next((f[3:] for f in flags if f.startswith("hd=")), None)
        if any(f != "unverified" and not f.startswith("hd=") for f in flags):
            raise InvalidToken("unknown test token flag")
        return Identity(uid=parts[1], email=parts[2], name=parts[2].split("@")[0],
                        email_verified="unverified" not in flags, hd=hd or None)


class JsonWorkspaceStore:
    """Workspaces, members and invites in memory, mirrored to one JSON file when a path is given."""

    def __init__(self, path: Path | None = None):
        self._path, self._lock = path, threading.RLock()
        self._ws: dict[str, Workspace] = {}
        self._members: dict[str, dict[str, Member]] = {}
        self._invites: dict[str, Invite] = {}
        if path and path.is_file():
            d = json.loads(path.read_text(encoding="utf-8"))
            self._ws = {k: Workspace.model_validate(v) for k, v in d.get("workspaces", {}).items()}
            self._members = {k: {u: Member.model_validate(m) for u, m in v.items()}
                             for k, v in d.get("members", {}).items()}
            self._invites = {k: Invite.model_validate(v) for k, v in d.get("invites", {}).items()}

    def _flush(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({
                "workspaces": {k: v.model_dump(mode="json") for k, v in self._ws.items()},
                "members": {k: {u: m.model_dump(mode="json") for u, m in v.items()}
                            for k, v in self._members.items()},
                "invites": {k: v.model_dump(mode="json") for k, v in self._invites.items()}}), encoding="utf-8")

    def get(self, workspace_id: str) -> Workspace | None:
        with self._lock:
            return self._ws.get(workspace_id)

    def create(self, ws: Workspace) -> bool:
        with self._lock:
            if ws.id in self._ws:
                return False
            self._ws[ws.id] = ws
            self._flush()
            return True

    def put(self, ws: Workspace) -> None:
        with self._lock:
            self._ws[ws.id] = ws
            self._flush()

    def delete(self, workspace_id: str) -> None:
        with self._lock:
            self._ws.pop(workspace_id, None)
            self._members.pop(workspace_id, None)
            self._invites = {c: i for c, i in self._invites.items() if i.workspace_id != workspace_id}
            self._flush()

    def find_by_domain(self, domain: str) -> Workspace | None:
        with self._lock:
            return next((w for w in self._ws.values() if w.domain and w.domain.lower() == domain.lower()), None)

    def list_all(self) -> list[Workspace]:
        with self._lock:
            return sorted(self._ws.values(), key=lambda w: w.created_at or datetime.min)

    def get_member(self, workspace_id: str, uid: str) -> Member | None:
        with self._lock:
            return self._members.get(workspace_id, {}).get(uid)

    def put_member(self, workspace_id: str, member: Member) -> None:
        with self._lock:
            self._members.setdefault(workspace_id, {})[member.uid] = member
            self._flush()

    def delete_member(self, workspace_id: str, uid: str) -> None:
        with self._lock:
            self._members.get(workspace_id, {}).pop(uid, None)
            self._flush()

    def members(self, workspace_id: str) -> list[Member]:
        with self._lock:
            return sorted(self._members.get(workspace_id, {}).values(), key=lambda m: m.joined_at or datetime.min)

    def memberships(self, uid: str) -> list[tuple[Workspace, Member]]:
        with self._lock:
            out = [(self._ws[w], ms[uid]) for w, ms in self._members.items() if uid in ms and w in self._ws]
        return sorted(out, key=lambda t: t[1].joined_at or datetime.min)

    def put_invite(self, invite: Invite) -> None:
        with self._lock:
            self._invites[invite.code] = invite
            self._flush()

    def get_invite(self, code: str) -> Invite | None:
        with self._lock:
            return self._invites.get(code)

    def delete_invite(self, code: str) -> None:
        with self._lock:
            self._invites.pop(code, None)
            self._flush()


_USER_DERIVED = {"workspaces", "platform_admin"}  # computed per request, never stored


class JsonUserStore:
    def __init__(self, path: Path | None = None):
        self._path, self._users, self._lock = path, {}, threading.Lock()
        if path and path.is_file():
            for u in json.loads(path.read_text(encoding="utf-8")):
                self._users[u["uid"]] = User.model_validate(u)

    def _flush(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps([u.model_dump(mode="json", exclude=_USER_DERIVED) for u in self._users.values()]),
                                  encoding="utf-8")

    def get(self, uid: str) -> User | None:
        with self._lock:
            return self._users.get(uid)

    def put(self, user: User) -> None:
        with self._lock:
            self._users[user.uid] = user
            self._flush()

    def list(self) -> list[User]:
        with self._lock:
            return list(self._users.values())

    def delete(self, uid: str) -> None:
        with self._lock:
            self._users.pop(uid, None)
            self._flush()


class JsonAuditLog:
    """Append-only JSON lines. The only rewrite is `delete_actor`, for a user's own 'delete my data'."""

    def __init__(self, path: Path | None = None):
        self._path, self._events, self._lock = path, [], threading.Lock()
        if path and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._events.append(AuditEvent.model_validate_json(line))

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(event.model_dump_json() + "\n")

    def list_for(self, workspace_id: str, limit: int, *, actor: str | None = None) -> list[AuditEvent]:
        with self._lock:
            mine = [e for e in self._events if e.workspace_id == workspace_id and (actor is None or e.actor == actor)]
        return sorted(mine, key=lambda e: e.at, reverse=True)[:limit]

    def list_all(self, limit: int) -> list[AuditEvent]:
        with self._lock:
            return sorted(self._events, key=lambda e: e.at, reverse=True)[:limit]

    def _drop(self, keep) -> int:
        with self._lock:
            kept = [e for e in self._events if keep(e)]
            removed = len(self._events) - len(kept)
            self._events = kept
            if self._path:
                self._path.write_text("".join(e.model_dump_json() + "\n" for e in kept), encoding="utf-8")
            return removed

    def delete_actor(self, actor: str) -> int:
        return self._drop(lambda e: e.actor != actor)

    def delete_workspace(self, workspace_id: str) -> int:
        return self._drop(lambda e: e.workspace_id != workspace_id)


class MemorySpendCounter:
    """Daily review counter for one process (CLOUD=local); the Firestore one is shared by every instance."""

    def __init__(self) -> None:
        self._n: dict[str, int] = {}
        self._lock = threading.Lock()

    def take(self, key: str, cap: int) -> bool:
        from app.spend import next_count

        with self._lock:
            new = next_count(self._n.get(key, 0), cap)
            if new is None:
                return False
            self._n[key] = new
            return True

    def used(self, key: str) -> int:
        with self._lock:
            return self._n.get(key, 0)


def local_stores(settings: Settings):
    root = settings.data_dir
    return (DiskBlobStore(root / "blobs"), MemoryIndex(root / "chunks.jsonl"),
            JsonReviewStore(root / "reviews"), MemoryCompanyStore(),
            LocalTokenVerifier(), JsonUserStore(root / "users.json"), JsonAuditLog(root / "audit.jsonl"),
            JsonWorkspaceStore(root / "workspaces.json"))
