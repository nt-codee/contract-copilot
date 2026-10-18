"""The knowledge store: law, cases and playbook as an indexed store, searched by keyword and by vector.

Pure Python (no cloud SDK). The store itself is a port with a local adapter here and a Google Cloud
adapter in app/adapters/gcp_knowledge.py. `HybridKnowledge` is what the agent and the routes use: it
wraps the in-memory `Knowledge` (always the fallback) and the store.

Search is hybrid: the vector top 12 and the keyword top 12 are merged by reciprocal-rank fusion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol

from app.knowledge import (TOP_K, Knowledge, LawEntry, PlaybookEntry, _has_words, _section, _wanted,
                           clip, parse_frontmatter, sections, tokens)
from app.models import KnowledgeEntry
from app.deeplinks import load_deeplinks
from app.ports import Embedder

log = logging.getLogger("cc.kbstore")

VECTOR_K = 12
KEYWORD_K = 12
RRF_K = 60
EMBED_TEXT_CHARS = 1500
EXCERPT_CHARS = 400
DOCS_TTL_S = 300


@dataclass
class KnowledgeDoc:
    id: str  # law id, or "playbook:<contract_type>/<clause_type>"
    kind: str  # law | case | playbook
    title: str
    jurisdiction: str | None = None
    contract_type: str | None = None
    clause_types: list[str] = field(default_factory=list)
    citation: str | None = None
    plain: str = ""
    excerpt: str = ""
    url: str | None = None  # the official source
    source_url: str | None = None  # deep link to the provision, else the plain url
    gcs_uri: str = ""
    kb_version: str = ""
    content_hash: str = ""
    embedding: list[float] = field(default_factory=list)
    body: str = ""  # the markdown file as ingested (not kept in Firestore; it lives in the bucket)
    text: str = ""  # the part that is embedded: a law's Text section, a playbook's body

    def embed_input(self) -> str:
        return f"{self.title}\n{self.plain}\n{self.text[:EMBED_TEXT_CHARS]}"


class KnowledgeStore(Protocol):
    def content_hashes(self) -> dict[str, str]: ...
    def upsert(self, docs: list[KnowledgeDoc]) -> None:
        """Write each doc's markdown to the blob store (setting gcs_uri) and its record, embedding included."""
        ...
    def list_docs(self) -> list[KnowledgeDoc]:
        """Every record, without body and embedding."""
        ...
    def get_doc(self, doc_id: str) -> KnowledgeDoc | None: ...
    def read_markdown(self, doc_id: str) -> bytes | None: ...
    def stream_markdown(self, doc_id: str) -> Iterator[bytes] | None: ...
    def vector_search(self, vector: list[float], kinds: list[str], limit: int) -> list[tuple[KnowledgeDoc, float]]: ...


# ---- building docs from the markdown files -------------------------------------------------------


def _hash(raw: str, embed_model: str) -> str:
    return hashlib.sha256(f"{embed_model}\n{raw}".encode("utf-8")).hexdigest()


def docs_from_files(knowledge_dir: Path, embed_model: str = "") -> list[KnowledgeDoc]:
    """Every law/case entry and playbook file under `knowledge_dir`, as unembedded docs."""
    links = load_deeplinks()
    docs: list[KnowledgeDoc] = []
    law_dir = knowledge_dir / "law"
    for path in sorted(law_dir.glob("*/*.md")) if law_dir.is_dir() else []:
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        text = _section(sections(body), "text")
        if not text:
            continue
        ct = meta.get("clause_types") or []
        url = str(meta.get("source_url") or "") or None
        eid = str(meta.get("id") or path.stem)
        docs.append(KnowledgeDoc(
            id=eid, kind="case" if meta.get("kind") == "case" else "law",
            jurisdiction=str(meta.get("jurisdiction") or path.parent.name), title=str(meta.get("title") or path.stem),
            citation=str(meta.get("citation") or "") or None, clause_types=list(ct if isinstance(ct, list) else [ct]),
            plain=_section(sections(body), "in plain words"), excerpt=clip(text, EXCERPT_CHARS), url=url,
            source_url=links.get(eid) or url,
            content_hash=_hash(raw, f"{embed_model}|{links.get(eid) or url}"), body=raw, text=text))
    pb_dir = knowledge_dir / "playbook"
    for path in sorted(pb_dir.glob("*/*.md")) if pb_dir.is_dir() else []:
        raw = path.read_text(encoding="utf-8").strip()
        title = next((ln.lstrip("# ").strip() for ln in raw.splitlines() if ln.startswith("# ")), path.stem)
        text = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("# ")).strip()
        docs.append(KnowledgeDoc(
            id=f"playbook:{path.parent.name}/{path.stem}", kind="playbook", title=title,
            contract_type=path.parent.name, clause_types=[path.stem],
            plain=_section(sections(raw), "in plain words"), excerpt=clip(text, EXCERPT_CHARS),
            content_hash=_hash(raw, embed_model), body=raw, text=text))
    return docs


def to_entry(d: KnowledgeDoc, *, text: str | None = None) -> KnowledgeEntry:
    return KnowledgeEntry(id=d.id, kind=d.kind, jurisdiction=d.jurisdiction, contract_type=d.contract_type,  # type: ignore[arg-type]
                          title=d.title, citation=d.citation, clause_types=list(d.clause_types),
                          plain=d.plain or None, text=text, url=d.url, source_url=d.source_url or d.url)


def detail_text(d: KnowledgeDoc, raw: str) -> str:
    """The markdown shown on the detail page: no frontmatter, no top heading."""
    _, body = parse_frontmatter(raw)
    return "\n".join(ln for ln in body.splitlines() if not ln.startswith("# ")).strip()


# ---- ranking --------------------------------------------------------------------------------------


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal-rank fusion: each ranking (best first) gives 1/(k+rank); sum per id, best first."""
    score: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            score[item] = score.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, len(first_seen))
    return sorted(score.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))


def bm25_rank(query: str, docs: dict[str, str], limit: int = KEYWORD_K) -> list[tuple[str, float]]:
    """BM25 over {id: text}; ids with a zero score are left out."""
    q = set(tokens(query))
    toks = {i: Counter(tokens(t)) for i, t in docs.items()}
    n = len(toks) or 1
    df: Counter = Counter()
    for tf in toks.values():
        df.update(tf.keys())
    avg = (sum(sum(tf.values()) for tf in toks.values()) / n) or 1.0
    out: list[tuple[str, float]] = []
    for i, tf in toks.items():
        dl = sum(tf.values()) or 1
        s = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * dl / avg))
                for t in q if tf.get(t))
        if s > 0:
            out.append((i, s))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---- the local adapter ----------------------------------------------------------------------------


class LocalKnowledgeStore:
    """Docs in memory, mirrored to `root` (docs.json plus kb/<version>/<id>.md) when a root is given."""

    def __init__(self, root: Path | None = None):
        self.root = root
        self._docs: dict[str, KnowledgeDoc] = {}
        self._lock = threading.Lock()
        if root and (root / "docs.json").is_file():
            for rec in json.loads((root / "docs.json").read_text(encoding="utf-8")):
                self._docs[rec["id"]] = KnowledgeDoc(**rec)

    def content_hashes(self) -> dict[str, str]:
        with self._lock:
            return {i: d.content_hash for i, d in self._docs.items()}

    def upsert(self, docs: list[KnowledgeDoc]) -> None:
        with self._lock:
            for d in docs:
                rel = f"kb/{d.kb_version}/{d.id}.md"
                d.gcs_uri = f"file://{rel}"
                if self.root:
                    path = self.root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(d.body, encoding="utf-8")
                self._docs[d.id] = d
            if self.root:
                self.root.mkdir(parents=True, exist_ok=True)
                recs = [{**d.__dict__, "body": ""} for d in self._docs.values()]
                (self.root / "docs.json").write_text(json.dumps(recs), encoding="utf-8")

    def _slim(self, d: KnowledgeDoc) -> KnowledgeDoc:
        return KnowledgeDoc(**{**d.__dict__, "embedding": [], "body": "", "text": ""})

    def list_docs(self) -> list[KnowledgeDoc]:
        with self._lock:
            return [self._slim(d) for d in self._docs.values()]

    def get_doc(self, doc_id: str) -> KnowledgeDoc | None:
        with self._lock:
            d = self._docs.get(doc_id)
            return self._slim(d) if d else None

    def read_markdown(self, doc_id: str) -> bytes | None:
        with self._lock:
            d = self._docs.get(doc_id)
        if d is None:
            return None
        if d.body:
            return d.body.encode("utf-8")
        if self.root and d.gcs_uri.startswith("file://"):
            path = self.root / d.gcs_uri[len("file://"):]  # taken from our own record, never from the request
            return path.read_bytes() if path.is_file() else None
        return None

    def stream_markdown(self, doc_id: str) -> Iterator[bytes] | None:
        data = self.read_markdown(doc_id)
        return iter([data]) if data is not None else None

    def vector_search(self, vector: list[float], kinds: list[str], limit: int) -> list[tuple[KnowledgeDoc, float]]:
        with self._lock:
            scored = [(self._slim(d), _cosine(vector, d.embedding)) for d in self._docs.values()
                      if d.kind in kinds and d.embedding]
        scored.sort(key=lambda t: (-t[1], t[0].id))
        return scored[:limit]


# ---- ingest ---------------------------------------------------------------------------------------


@dataclass
class IngestCounts:
    total: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    orphans: int = 0  # in the store but no longer in the files; left alone

    def __str__(self) -> str:
        return (f"{self.total} entries: {self.new} new, {self.updated} updated, {self.unchanged} unchanged, "
                f"{self.orphans} in the store but not in the files")


def ingest(store: KnowledgeStore, docs: list[KnowledgeDoc], embedder: Embedder, kb_version: str) -> IngestCounts:
    """Upload and embed every doc whose content hash changed. Idempotent: a second run changes nothing."""
    have = store.content_hashes()
    counts = IngestCounts(total=len(docs), orphans=len(set(have) - {d.id for d in docs}))
    todo: list[KnowledgeDoc] = []
    for d in docs:
        if have.get(d.id) == d.content_hash:
            counts.unchanged += 1
        else:
            counts.updated += d.id in have
            counts.new += d.id not in have
            todo.append(d)
    if todo:
        vectors, _ = embedder.embed([d.embed_input() for d in todo])
        for d, v in zip(todo, vectors, strict=True):
            if len(v) != embedder.dim:
                raise ValueError(f"embedding has {len(v)} dims, expected {embedder.dim}")
            d.embedding, d.kb_version = v, kb_version
        store.upsert(todo)
    return counts


# ---- what the agent and the routes use ---------------------------------------------------------------


class HybridKnowledge:
    """Same search interface as `Knowledge`, plus the store. Every store failure falls back to memory."""

    def __init__(self, kb: Knowledge, store: KnowledgeStore | None = None, embedder: Embedder | None = None):
        self.kb, self.store, self.embedder = kb, store, embedder
        self._docs_cache: tuple[float, list[KnowledgeDoc]] | None = None
        self._lock = threading.Lock()

    # passed through, so the agent can treat this like Knowledge
    @property
    def law(self) -> list[LawEntry]:
        return self.kb.law

    @property
    def playbook(self) -> dict[str, PlaybookEntry]:
        return self.kb.playbook

    def playbook_entry(self, contract_type: str, clause_type: str) -> PlaybookEntry | None:
        return self.kb.playbook_entry(contract_type, clause_type)

    def law_by_id(self, ref: str) -> LawEntry | None:
        return self.kb.law_by_id(ref)

    # ---- search ------------------------------------------------------------------------------------

    def _vector_hits(self, query: str, kinds: list[str]) -> list[tuple[KnowledgeDoc, float]]:
        if self.store is None or self.embedder is None:
            return []
        try:
            vectors, _ = self.embedder.embed([query], query=True)
            return self.store.vector_search(vectors[0], kinds, VECTOR_K * 2)
        except Exception:  # Firestore or the embedder is down: keyword search still works
            log.warning("vector search unavailable; keyword only", exc_info=True)
            return []

    def search_law_scored(self, query: str, jurisdiction: str, clause_type: str | None = None,
                          limit: int = TOP_K) -> list[tuple[LawEntry, float]]:
        keyword = self.kb.search_law_scored(query, jurisdiction, clause_type, KEYWORD_K)
        vector = [(d, s) for d, s in self._vector_hits(query, ["law", "case"]) if d.jurisdiction == jurisdiction]
        if not vector:
            return keyword[:limit]
        fused = rrf([[e.id for e, _ in keyword], [d.id for d, _ in vector[:VECTOR_K]]])
        out = [(e, s) for i, s in fused if (e := self.kb.law_by_id(i)) is not None]
        return out[:limit]

    def search_law(self, query: str, jurisdiction: str, clause_type: str | None = None,
                   limit: int = TOP_K) -> list[LawEntry]:
        return [e for e, _ in self.search_law_scored(query, jurisdiction, clause_type, limit)]

    def related_playbook(self, contract_type: str, clause_type: str, limit: int = 2) -> list[PlaybookEntry]:
        """Closest playbook entries of this contract type when there is no exact one (hybrid, RRF)."""
        mine = {k: e for k, e in self.kb.playbook.items() if k.startswith(f"{contract_type}/")}
        if not mine:
            return []
        query = f"{clause_type.replace('_', ' ')} {contract_type.replace('_', ' ')}"
        kw = bm25_rank(query, {k: f"{e.title} {e.plain} {e.text}" for k, e in mine.items()})
        vec = [(d, s) for d, s in self._vector_hits(query, ["playbook"]) if d.contract_type == contract_type]
        fused = rrf([[k for k, _ in kw], [d.id.removeprefix("playbook:") for d, _ in vec[:VECTOR_K]]])
        return [mine[k] for k, _ in fused if k in mine][:limit]

    # ---- browsing ----------------------------------------------------------------------------------

    def _docs(self) -> list[KnowledgeDoc]:
        if self.store is None:
            return []
        with self._lock:
            if self._docs_cache and time.monotonic() - self._docs_cache[0] < DOCS_TTL_S:
                return self._docs_cache[1]
        try:
            docs = self.store.list_docs()
        except Exception:
            log.warning("knowledge store unavailable; serving from memory", exc_info=True)
            return []
        with self._lock:
            self._docs_cache = (time.monotonic(), docs)
        return docs

    def entries(self, *, kind: str | None = None, jurisdiction: str | None = None,
                contract_type: str | None = None, q: str = "") -> list[KnowledgeEntry]:
        docs = self._docs()
        if not docs:
            return self.kb.entries(kind=kind, jurisdiction=jurisdiction, contract_type=contract_type, q=q)
        words = tokens(q)
        out = []
        for d in docs:
            e = to_entry(d)
            hay = f"{d.title} {d.citation or ''} {d.plain} {d.excerpt} {' '.join(d.clause_types)}"
            if _wanted(e, kind, jurisdiction, contract_type) and _has_words(words, hay):
                out.append(e)
        order = {"law": 0, "case": 1, "playbook": 2}
        out.sort(key=lambda k: (order[k.kind], k.jurisdiction or "", k.contract_type or "", k.title))
        return out

    def entry(self, entry_id: str) -> KnowledgeEntry | None:
        if self.store is not None:
            try:
                doc = self.store.get_doc(entry_id)
                raw = self.store.read_markdown(entry_id) if doc else None
                if doc and raw is not None:
                    return to_entry(doc, text=detail_text(doc, raw.decode("utf-8")))
            except Exception:
                log.warning("knowledge store read failed for %s; serving from memory", entry_id, exc_info=True)
        return self.kb.entry(entry_id)

    def raw(self, entry_id: str) -> Iterator[bytes] | None:
        """The stored markdown file, streamed. Falls back to the in-memory body."""
        if self.store is not None:
            try:
                stream = self.store.stream_markdown(entry_id)
                if stream is not None:
                    return stream
            except Exception:
                log.warning("knowledge store stream failed for %s; serving from memory", entry_id, exc_info=True)
        e = self.kb.entry(entry_id)
        return iter([(e.text or "").encode("utf-8")]) if e else None
