"""Google Cloud adapters: Gemini on Vertex AI, GCS, Firestore (documents + vector search).

SDKs are imported lazily inside constructors so CLOUD=local LLM=fake never loads them.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError

from app.config import EMBED_DIM, Settings
from app.models import CompanyDoc, ReviewResult
from app.ports import (ChatMessage, Chunk, CompanyChunk, LLMUsage, PdfPart, T, ToolCall, ToolSpec,
                       UpstreamError)

log = logging.getLogger("cc.gcp")

# SDK-level HTTP retries on these statuses and on timeouts. Generation is bounded hard: a call that
# stalls for 45 s is abandoned and retried, at most 3 tries (waits 2 s, 4 s). Embeddings get 5 tries.
LLM_ATTEMPTS = 3
EMBED_ATTEMPTS = 5
GENERATE_TIMEOUT_MS = 35_000  # review call: normally 5-15 s
AGENT_TIMEOUT_MS = 20_000  # tool-planning turn: normally 3-4 s, so a stall retries quickly
TRANSCRIBE_TIMEOUT_MS = 120_000  # a whole scanned PDF in one call legitimately takes longer
EMBED_TIMEOUT_MS = 30_000
RETRY_STATUSES = [429, 500, 503, 504]
EMBED_WORKERS = 8


def _retry_options(attempts: int = LLM_ATTEMPTS, timeout_ms: int = GENERATE_TIMEOUT_MS):
    from google.genai import types

    return types.HttpOptions(
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(attempts=attempts, initial_delay=2.0, max_delay=20.0,
                                             exp_base=2.0, http_status_codes=RETRY_STATUSES),
    )


def _upstream_failure(what: str, e: Exception, user_message: str) -> UpstreamError:
    """Log the upstream status and message for operators; the user only sees `user_message`."""
    status = getattr(e, "code", None)
    status = status if isinstance(status, int) else None
    log.warning("%s failed after retries: status=%s message=%s", what, status,
                str(getattr(e, "message", None) or e)[:300])
    return UpstreamError(user_message, status)


class GeminiLLM:
    def __init__(self, settings: Settings):
        from google import genai

        self.model = settings.gemini_model
        self._thinking = settings.gemini_thinking
        self.calls = 0
        self._client = genai.Client(vertexai=True, project=settings.gcp_project,
                                    location=settings.gemini_location, http_options=_retry_options())
        # Optional: write each raw response to RECORD_DIR/<purpose>.json, to refresh test fixtures.
        self._record_dir = os.environ.get("RECORD_DIR") or None

    def _config(self, system: str, purpose: str = "", **extra):
        from google.genai import types

        timeout = (TRANSCRIBE_TIMEOUT_MS if purpose == "transcribe"
                   else AGENT_TIMEOUT_MS if purpose == "agent" else GENERATE_TIMEOUT_MS)
        return types.GenerateContentConfig(
            system_instruction=system,
            http_options=_retry_options(LLM_ATTEMPTS, timeout),
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking) if self._thinking else None,
            **extra,
        )

    def _call(self, purpose: str, contents, config):
        self.calls += 1
        t0 = time.monotonic()
        try:
            resp = self._client.models.generate_content(model=self.model, contents=contents, config=config)
        except Exception as e:
            raise _upstream_failure(f"gemini {purpose} call", e,
                                    "The AI service did not respond. Please try again in a minute.") from e
        um = resp.usage_metadata
        log.info("gemini %s: %.1fs (in %s, out %s, thinking %s tokens)", purpose, time.monotonic() - t0,
                 um.prompt_token_count if um else "?", um.candidates_token_count if um else "?",
                 um.thoughts_token_count if um else "?")
        return resp

    def _usage(self, resp) -> LLMUsage:
        um = resp.usage_metadata
        return LLMUsage(
            model=self.model,
            input_tokens=(um.prompt_token_count or 0) if um else 0,
            # thinking tokens are billed as output
            output_tokens=((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0,
        )

    def chat(self, *, system: str, messages: list[ChatMessage], tools: list[ToolSpec],
             purpose: str, force_tools: bool = False) -> tuple[ChatMessage, LLMUsage]:
        """One function-calling turn. We run the loop ourselves, so automatic calling is off."""
        from google.genai import types

        config = self._config(
            system, purpose,
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY" if force_tools else "AUTO")),
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters_json_schema=t.parameters) for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self._call(purpose, [self._content(m) for m in messages], config)
        cands = resp.candidates or []
        if not cands or cands[0].content is None:
            log.warning("gemini %s returned no content (finish_reason=%s)", purpose,
                        cands[0].finish_reason if cands else None)
            raise UpstreamError("The AI service returned an unexpected answer. Please try again.")
        content = cands[0].content
        parts = content.parts or []
        calls = [ToolCall(name=p.function_call.name, args=dict(p.function_call.args or {}),
                          id=p.function_call.id) for p in parts if p.function_call]
        text = "".join(p.text for p in parts if p.text and not p.thought)
        return ChatMessage(role="model", text=text, tool_calls=calls, raw=content), self._usage(resp)

    @staticmethod
    def _content(m: ChatMessage):
        from google.genai import types

        if m.role == "model" and m.raw is not None:
            return m.raw  # as returned, so thought signatures go back unchanged
        if m.role == "model":
            parts = ([types.Part.from_text(text=m.text)] if m.text else []) + [
                types.Part(function_call=types.FunctionCall(name=c.name, args=c.args, id=c.id))
                for c in m.tool_calls]
            return types.Content(role="model", parts=parts)
        if m.role == "tool":
            return types.Content(role="user", parts=[
                types.Part(function_response=types.FunctionResponse(name=r.name, response=r.result, id=r.id))
                for r in m.tool_results])
        return types.Content(role="user", parts=[types.Part.from_text(text=m.text)])

    def generate(self, *, system: str, parts: list[str | PdfPart], schema: type[T],
                 purpose: str) -> tuple[T, LLMUsage]:
        from google.genai import types

        contents = [
            types.Part.from_bytes(data=p.data, mime_type=p.mime_type) if isinstance(p, PdfPart)
            else types.Part.from_text(text=p)
            for p in parts
        ]
        config = self._config(system, purpose, response_mime_type="application/json", response_schema=schema)
        resp = self._call(purpose, contents, config)

        text = resp.text or ""
        if self._record_dir:
            Path(self._record_dir).mkdir(parents=True, exist_ok=True)
            Path(self._record_dir, f"{purpose}.json").write_text(text, encoding="utf-8")
        try:
            parsed = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(text)
        except ValidationError as e:
            log.error("gemini %s returned an unexpected shape: %s", purpose, str(e)[:500])
            raise UpstreamError("The AI service returned an unexpected answer. Please try again.") from e
        return parsed, self._usage(resp)


class GeminiEmbedder:
    """gemini-embedding-2 accepts ONE content per request (verified 2026-10-18), so we fan out."""

    def __init__(self, settings: Settings):
        from google import genai

        self.model = settings.embed_model
        self.dim = EMBED_DIM
        self.calls = 0
        self._client = genai.Client(vertexai=True, project=settings.gcp_project,
                                    location=settings.embed_location,
                                    http_options=_retry_options(EMBED_ATTEMPTS, EMBED_TIMEOUT_MS))

    def _one(self, text: str, task: str) -> tuple[list[float], int]:
        from google.genai import types

        self.calls += 1
        r = self._client.models.embed_content(
            model=self.model, contents=text,
            config=types.EmbedContentConfig(output_dimensionality=self.dim, task_type=task),
        )
        emb = r.embeddings[0]
        tokens = int(emb.statistics.token_count) if emb.statistics and emb.statistics.token_count else len(text) // 4
        return list(emb.values), tokens

    def embed(self, texts: list[str], *, query: bool = False) -> tuple[list[list[float]], int]:
        task = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
        try:
            with ThreadPoolExecutor(max_workers=min(EMBED_WORKERS, max(len(texts), 1))) as pool:
                results = list(pool.map(lambda t: self._one(t, task), texts))
        except Exception as e:
            raise _upstream_failure("gemini embedding", e,
                                    "Search is unavailable right now. Please try again in a minute.") from e
        vectors = [v for v, _ in results]
        for v in vectors:
            if len(v) != self.dim:
                raise UpstreamError("Search is misconfigured (embedding size mismatch).")
        return vectors, sum(t for _, t in results)


class GcsBlobStore:
    def __init__(self, settings: Settings):
        from google.cloud import storage

        if not settings.upload_bucket:
            raise RuntimeError("UPLOAD_BUCKET must be set when CLOUD=gcp")
        self._bucket = storage.Client(project=settings.gcp_project).bucket(settings.upload_bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> None:
        self._bucket.blob(key).upload_from_string(data, content_type=content_type)

    def get(self, key: str) -> bytes | None:
        from google.api_core.exceptions import NotFound

        try:
            return self._bucket.blob(key).download_as_bytes()
        except NotFound:
            return None

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(key).delete()
        except NotFound:
            pass


def _firestore_client(settings: Settings):
    from google.cloud import firestore

    return firestore.Client(project=settings.gcp_project, database=settings.firestore_db)


def _to_firestore(review: ReviewResult) -> dict:
    """Firestore rejects arrays of arrays: store each box [x0,y0,x1,y1] as a map."""
    d = review.model_dump(mode="json")
    for f in d["findings"]:
        f["boxes"] = [dict(zip(("x0", "y0", "x1", "y1"), b)) for b in f.get("boxes", [])]
    return d


def _from_firestore(d: dict) -> ReviewResult:
    d = dict(d)
    d["findings"] = [
        {**f, "boxes": [[b["x0"], b["y0"], b["x1"], b["y1"]] if isinstance(b, dict) else b
                        for b in f.get("boxes", [])]}
        for f in d.get("findings", [])
    ]
    return ReviewResult.model_validate(d)


class FirestoreReviewStore:
    COLLECTION = "reviews"

    def __init__(self, settings: Settings):
        self._col = _firestore_client(settings).collection(self.COLLECTION)

    def save(self, review: ReviewResult) -> None:
        self._col.document(review.id).set(_to_firestore(review))

    def get(self, review_id: str) -> ReviewResult | None:
        snap = self._col.document(review_id).get()
        return _from_firestore(snap.to_dict()) if snap.exists else None

    def list(self, limit: int = 50) -> list[ReviewResult]:
        from google.cloud import firestore

        q = self._col.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
        return [_from_firestore(s.to_dict()) for s in q.stream()]

    def find_by_sample(self, sample_id: str) -> ReviewResult | None:
        from google.cloud.firestore_v1.base_query import FieldFilter

        # newest wins (so ?refresh=1 replaces the cache); sorted here to avoid a composite index
        docs = [s.to_dict() for s in self._col.where(filter=FieldFilter("sample_id", "==", sample_id)).stream()]
        if not docs:
            return None
        return _from_firestore(max(docs, key=lambda d: str(d.get("created_at", ""))))


class FirestoreIndex:
    """Collection `chunks`, vector field `embedding` (768 dims, COSINE). The index is created by infra."""

    COLLECTION = "chunks"
    FIELD = "embedding"

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._col = self._db.collection(self.COLLECTION)

    def upsert(self, chunks: list[Chunk]) -> None:
        from google.cloud.firestore_v1.vector import Vector

        for start in range(0, len(chunks), 400):  # Firestore batch limit is 500 writes
            batch = self._db.batch()
            for c in chunks[start:start + 400]:
                batch.set(self._col.document(c.id), {
                    "review_id": c.review_id, "filename": c.filename, "page": c.page, "text": c.text,
                    "clause_type": c.clause_type, "finding_id": c.finding_id,
                    self.FIELD: Vector(c.embedding),
                })
            batch.commit()

    def query(self, vector: list[float], limit: int) -> list[tuple[Chunk, float]]:
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        q = self._col.find_nearest(vector_field=self.FIELD, query_vector=Vector(vector),
                                   distance_measure=DistanceMeasure.COSINE, limit=limit,
                                   distance_result_field="vector_distance")
        out: list[tuple[Chunk, float]] = []
        for s in q.stream():
            d = s.to_dict()
            chunk = Chunk(id=s.id, review_id=d["review_id"], filename=d["filename"], page=d["page"],
                          text=d["text"], clause_type=d.get("clause_type"), finding_id=d.get("finding_id"))
            out.append((chunk, 1.0 - float(d.get("vector_distance", 1.0))))
        return out



class FirestoreCompanyStore:
    """Collections `company_docs` (metadata) and `company_chunks` (text + 768-d `embedding`, COSINE).

    The vector index on company_chunks.embedding must exist (created by infrastructure/gcp/app).
    """

    DOCS = "company_docs"
    CHUNKS = "company_chunks"
    FIELD = "embedding"

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._docs = self._db.collection(self.DOCS)
        self._chunks = self._db.collection(self.CHUNKS)

    def put(self, doc: CompanyDoc, chunks: list[CompanyChunk]) -> None:
        from google.cloud.firestore_v1.vector import Vector

        for start in range(0, len(chunks), 400):
            batch = self._db.batch()
            for c in chunks[start:start + 400]:
                batch.set(self._chunks.document(c.id), {
                    "doc_id": c.doc_id, "title": c.title, "page": c.page, "text": c.text,
                    self.FIELD: Vector(c.embedding),
                })
            batch.commit()
        self._docs.document(doc.id).set(doc.model_dump(mode="json"))  # last: a doc is listed only when searchable

    def list(self) -> list[CompanyDoc]:
        from google.cloud import firestore

        q = self._docs.order_by("created_at", direction=firestore.Query.DESCENDING)
        return [CompanyDoc.model_validate(s.to_dict()) for s in q.stream()]

    def get(self, doc_id: str) -> CompanyDoc | None:
        snap = self._docs.document(doc_id).get()
        return CompanyDoc.model_validate(snap.to_dict()) if snap.exists else None

    def delete(self, doc_id: str) -> bool:
        from google.cloud.firestore_v1.base_query import FieldFilter

        ref = self._docs.document(doc_id)
        existed = ref.get().exists
        refs = [s.reference for s in self._chunks.where(filter=FieldFilter("doc_id", "==", doc_id)).stream()]
        for start in range(0, len(refs), 400):
            batch = self._db.batch()
            for r in refs[start:start + 400]:
                batch.delete(r)
            batch.commit()
        ref.delete()
        return existed

    def search(self, vector: list[float], limit: int) -> list[tuple[CompanyChunk, float]]:
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        q = self._chunks.find_nearest(vector_field=self.FIELD, query_vector=Vector(vector),
                                      distance_measure=DistanceMeasure.COSINE, limit=limit,
                                      distance_result_field="vector_distance")
        out: list[tuple[CompanyChunk, float]] = []
        for s in q.stream():
            d = s.to_dict()
            out.append((CompanyChunk(id=s.id, doc_id=d["doc_id"], title=d["title"], page=d["page"],
                                     text=d["text"]), 1.0 - float(d.get("vector_distance", 1.0))))
        return out
