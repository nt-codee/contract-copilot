"""HTTP routes. Imports ports/pipeline only; adapters are chosen in app.adapters.build_services.

Run: uvicorn --factory app.main:create_app
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import pipeline
from app.adapters import build_services
from app.config import Settings
from app.models import CompanyDoc, KnowledgeEntry, NegotiationDraft, ReviewResult, ReviewSummary, Sample, SearchHit
from app.pipeline import Services, UserError
from app.ports import UpstreamError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cc.api")


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class RateLimiter:
    """Sliding one-hour window per client key, in memory (per instance)."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                return False
            q.append(now)
            return True


def client_ip(request: Request) -> str:
    # Cloud Run's front end appends the real client address as the LAST X-Forwarded-For entry;
    # earlier entries are client-supplied and can be spoofed.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or Settings()
    svc = services or build_services(settings)
    limiter = RateLimiter(settings.rate_limit_per_hour)
    sample_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
    negotiation_cache: dict[str, NegotiationDraft] = {}

    app = FastAPI(title="Contract Review Co-pilot API", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.services = svc
    app.state.limiter = limiter

    if settings.dev_cors:
        app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                           allow_methods=["*"], allow_headers=["*"])

    # ---- errors: plain words, never a stack trace ----------------------------------------
    @app.exception_handler(UserError)
    async def _user_error(_: Request, e: UserError):
        return _err(e.status, e.code, e.message)

    @app.exception_handler(UpstreamError)
    async def _upstream_error(_: Request, e: UpstreamError):
        log.warning("upstream failure shown to the user: %s (upstream status %s)", e, e.status)
        return _err(502, "upstream_unavailable", str(e))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        missing_file = any(err.get("loc", [None])[-1] == "file" for err in e.errors())
        msg = "Please attach a PDF file." if missing_file else "Some of the request details are missing or invalid."
        return _err(422, "invalid_request", msg)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, e: StarletteHTTPException):
        messages = {404: "Not found.", 405: "That method is not allowed here."}
        return _err(e.status_code, f"http_{e.status_code}", messages.get(e.status_code, str(e.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        log.exception("unhandled error")
        return _err(500, "internal_error", "Something went wrong on our side. Please try again.")

    # ---- helpers -------------------------------------------------------------------------
    def check_rate(request: Request) -> None:
        if not limiter.allow(client_ip(request)):
            raise UserError(429, "rate_limited",
                            f"You have reached the limit of {limiter.per_hour} reviews per hour. "
                            "Please try again later.")

    def load_samples() -> list[dict]:
        p = settings.samples_dir / "index.json"
        if not p.is_file():
            return []
        return json.loads(p.read_text(encoding="utf-8"))

    def sample_pdf_path(entry: dict) -> Path | None:
        name = entry.get("file") or f"{entry['id']}.pdf"
        root = settings.samples_dir.resolve()
        for cand in (settings.samples_dir / name, settings.samples_dir / Path(name).name):
            cand = cand.resolve()
            if cand.is_relative_to(root) and cand.is_file():
                return cand
        return None

    def get_review_or_404(review_id: str) -> ReviewResult:
        r = svc.store.get(review_id)
        if r is None:
            raise UserError(404, "review_not_found", "We could not find that review.")
        return r

    # ---- routes --------------------------------------------------------------------------
    # Cloud Run's frontend reserves paths ending in "z" (/healthz 404s there), so prod probes /api/health.
    @app.get("/api/health")
    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    async def read_pdf_upload(request: Request, file: UploadFile) -> bytes:
        """Read an uploaded PDF within the size cap, validate it, and spend one rate-limit slot."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_bytes + 64 * 1024:
            raise UserError(413, "file_too_large",
                            f"That file is larger than {settings.max_bytes // (1024 * 1024)} MB. "
                            "Please upload a smaller PDF.")
        data = await file.read(settings.max_bytes + 1)
        # validate before spending a rate-limit slot on a file we would reject anyway
        pipeline.validate_pdf(data, settings).close()
        check_rate(request)
        return data

    @app.post("/api/reviews", response_model=ReviewResult)
    async def create_review(request: Request, file: UploadFile = File(...)):
        data = await read_pdf_upload(request, file)
        return await run_in_threadpool(pipeline.review, svc, data, file.filename or "contract.pdf")

    # ---- company knowledge base. No auth: one demo tenant, so every visitor shares these documents.
    @app.post("/api/company/docs", response_model=CompanyDoc)
    async def add_company_doc(request: Request, file: UploadFile = File(...)):
        data = await read_pdf_upload(request, file)
        return await run_in_threadpool(pipeline.add_company_doc, svc, data, file.filename or "document.pdf")

    @app.get("/api/company/docs", response_model=list[CompanyDoc])
    def list_company_docs():
        return svc.company.list()

    @app.delete("/api/company/docs/{doc_id}", status_code=204)
    def delete_company_doc(doc_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id):
            raise UserError(404, "company_doc_not_found", "We could not find that document.")
        pipeline.delete_company_doc(svc, doc_id)
        return Response(status_code=204)

    @app.get("/api/samples", response_model=list[Sample])
    def list_samples():
        return [Sample.model_validate(s) for s in load_samples()]

    @app.post("/api/reviews/sample/{sample_id}", response_model=ReviewResult)
    def review_sample(sample_id: str, request: Request, refresh: bool = False):
        """Cached after the first run; ?refresh=1 re-runs it (and counts against the rate limit)."""
        entry = next((s for s in load_samples() if s.get("id") == sample_id), None)
        if entry is None:
            raise UserError(404, "sample_not_found", "We could not find that sample contract.")
        with sample_locks[sample_id]:
            cached = None if refresh else svc.store.find_by_sample(sample_id)
            if cached is not None:
                return cached
            path = sample_pdf_path(entry)
            if path is None:
                raise UserError(404, "sample_not_found", "That sample contract file is missing.")
            check_rate(request)
            return pipeline.review(svc, path.read_bytes(), path.name, sample_id=sample_id)

    @app.get("/api/reviews", response_model=list[ReviewSummary])
    def list_reviews(limit: int = Query(50, ge=1, le=200)):
        return [ReviewSummary.of(r) for r in svc.store.list(limit)]

    @app.get("/api/reviews/{review_id}", response_model=ReviewResult)
    def get_review(review_id: str):
        return get_review_or_404(review_id)

    @app.post("/api/reviews/{review_id}/negotiation", response_model=NegotiationDraft)
    def negotiate(review_id: str):
        r = get_review_or_404(review_id)
        if review_id not in negotiation_cache:  # one LLM call per review per instance
            negotiation_cache[review_id] = pipeline.negotiation(svc, r)
        return negotiation_cache[review_id]

    @app.get("/api/reviews/{review_id}/pdf")
    def get_pdf(review_id: str):
        get_review_or_404(review_id)
        data = svc.blobs.get(f"uploads/{review_id}.pdf")
        if data is None:
            raise UserError(404, "pdf_not_found", "The original PDF is no longer stored (uploads are kept 7 days).")
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{review_id}.pdf"'})

    @app.get("/api/search", response_model=list[SearchHit])
    def search(q: str = Query(..., min_length=1, max_length=500), limit: int = Query(10, ge=1, le=50)):
        return pipeline.search(svc, q.strip(), limit)

    # ---- the library: law, cases and playbook, as users browse it
    @app.get("/api/knowledge", response_model=list[KnowledgeEntry], response_model_exclude_none=False)
    def list_knowledge(kind: str | None = Query(None, pattern="^(law|case|playbook)$"),
                       jurisdiction: str | None = Query(None, pattern="^(my|sg)$"),
                       contract_type: str | None = Query(None, max_length=40),
                       q: str = Query("", max_length=200)):
        return svc.kb.entries(kind=kind, jurisdiction=jurisdiction, contract_type=contract_type, q=q)

    @app.get("/api/knowledge/{entry_id:path}", response_model=KnowledgeEntry)
    def get_knowledge(entry_id: str):
        entry = svc.kb.entry(entry_id)
        if entry is None:
            raise UserError(404, "knowledge_not_found", "We could not find that entry.")
        return entry

    # ---- static web app with SPA fallback ------------------------------------------------
    dist = settings.web_dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path == "api":
            raise UserError(404, "not_found", "Not found.")
        index = dist / "index.html"
        if path:
            target = (dist / path).resolve()
            if target.is_relative_to(dist) and target.is_file():
                return FileResponse(target)
        if index.is_file():
            return FileResponse(index)
        return _err(404, "web_not_built", "The web app is not built in this image.")

    return app

