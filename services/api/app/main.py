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

from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import admin, pipeline, routes_knowledge, workspaces
from app.adapters import build_services
from app.audit import now as audit_now
from app.auth import (SIGN_IN_MESSAGE, Actor, get_actor, require_admin, require_policy_manager,
                      require_writer)
from app.config import DEMO_OWNER, Settings
from app.models import (AuditEvent, CompanyDoc, Identity, Invite, Member, NegotiationDraft, ReviewResult,
                        ReviewSummary, Sample, SearchHit, User, Workspace, WorkspaceRef)
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


class WorkspacePatch(BaseModel):
    name: str | None = None
    domain: str | None = None  # Google Workspace domain for auto-join; "" clears it


class RoleBody(BaseModel):
    role: Literal["owner", "admin", "member"]


class InviteBody(BaseModel):
    role: Literal["admin", "member"] = "member"


class EarlySignIn:
    """Pure ASGI (a BaseHTTPMiddleware would swallow the app's own 500 handler): an upload with no
    credentials is answered 401 before its body is read."""

    PATHS = ("/api/reviews", "/api/company/docs")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] in self.PATHS:
            names = {k for k, _ in scope["headers"]}
            if b"authorization" not in names and b"x-workspace-id" not in names:
                return await _err(401, "sign_in_required", SIGN_IN_MESSAGE)(scope, receive, send)
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or Settings()
    svc = services or build_services(settings)
    limiter = RateLimiter(settings.rate_limit_per_hour)
    sample_locks: dict[tuple[str, str], threading.Lock] = defaultdict(threading.Lock)
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

    def get_review_or_404(review_id: str, actor: Actor) -> ReviewResult:
        """A review in another workspace is reported as not found, never as forbidden."""
        r = svc.store.get(review_id, owner=actor.owner)
        if r is None:
            raise UserError(404, "review_not_found", "We could not find that review.")
        return r

    def note(actor: Actor, action: str, target_id: str | None = None, label: str | None = None,
            detail: dict | None = None) -> None:
        """One audit line, in the actor's active workspace."""
        svc.audit.record(actor.audit_actor, action, target_id, label, detail, workspace_id=actor.owner)

    def audits_views(actor: Actor) -> bool:
        """Views and exports are logged for signed-in users and the seeder, not for anonymous demo visitors
        (that would be an unbounded public write path)."""
        return actor.signed_in or actor.seeding

    # Refuse an anonymous upload before reading its body (the route re-checks authoritatively).
    app.add_middleware(EarlySignIn)

    # ---- routes --------------------------------------------------------------------------
    # Cloud Run's frontend reserves paths ending in "z" (/healthz 404s there), so prod probes /api/health.
    @app.get("/api/health")
    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ---- sign-in -------------------------------------------------------------------------
    @app.get("/api/auth/config")
    def auth_config():
        """The Identity Platform client config (public by design), so the web needs no per-environment rebuild."""
        if not settings.identity_web_config:
            raise UserError(404, "auth_not_configured", "Sign-in is not set up on this server.")
        try:
            return JSONResponse(json.loads(settings.identity_web_config))
        except ValueError:
            log.error("IDENTITY_WEB_CONFIG is not valid JSON")
            raise UserError(404, "auth_not_configured", "Sign-in is not set up on this server.") from None

    @app.get("/api/me", response_model=User)
    def me(actor: Actor = Depends(get_actor)):
        """Who am I, my workspaces and roles. The web calls this on load; it is also where last_seen and
        `signed_in` are recorded."""
        if actor.user is None:
            raise UserError(401, "sign_in_required", SIGN_IN_MESSAGE)
        u, now = actor.user, audit_now()
        prev = svc.users.get(u.uid)
        stored = User(uid=u.uid, email=u.email, name=u.name, photo_url=u.photo_url,
                      created_at=prev.created_at if prev and prev.created_at else now, last_seen_at=now)
        if (prev is None or prev.last_seen_at is None or now - prev.last_seen_at > timedelta(minutes=5)
                or (prev.email, prev.name, prev.photo_url) != (u.email, u.name, u.photo_url)):
            svc.users.put(stored)
        if prev is None or prev.last_seen_at is None or now - prev.last_seen_at > timedelta(minutes=30):
            note(actor, "signed_in", None, None, {"app_version": settings.app_version})
        return workspaces.user_view(svc, u, actor.admin, stored)

    @app.post("/api/me/delete")
    def delete_me(actor: Actor = Depends(get_actor)):
        """Delete everything this user owns. Their own audit events go too; one `deleted_my_data` line stays."""
        if actor.user is None:
            raise UserError(401, "sign_in_required", SIGN_IN_MESSAGE)
        counts, review_ids = pipeline.delete_my_data(svc, actor.user.uid)
        for rid in review_ids:
            negotiation_cache.pop(rid, None)
        note(actor, "deleted_my_data", None, None, counts)
        return {"deleted": counts}

    @app.get("/api/activity", response_model=list[AuditEvent])
    def activity(limit: int = Query(50, ge=1, le=500), actor: Actor = Depends(get_actor)):
        """The active workspace's audit trail: owners and admins see everyone's actions, a member sees
        their own. A demo visitor sees the demo workspace's."""
        only = actor.uid if actor.signed_in and not actor.manages and not actor.seeding else None
        return svc.audit.for_workspace(actor.owner, limit, actor=only)

    # ---- reviews -------------------------------------------------------------------------
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
    async def create_review(request: Request, file: UploadFile = File(...), actor: Actor = Depends(get_actor)):
        require_writer(actor)
        data = await read_pdf_upload(request, file)
        name = file.filename or "contract.pdf"
        result = await run_in_threadpool(pipeline.review, svc, data, name, None, actor.owner, actor.uid)
        note(actor, "uploaded_contract", result.id, name,
                         {"bytes": len(data), "pages": result.pages})
        pipeline.audit_review(svc, actor.audit_actor, result)
        return result

    # ---- company knowledge base: each workspace has its own documents ----------------------------
    @app.post("/api/company/docs", response_model=CompanyDoc)
    async def add_company_doc(request: Request, file: UploadFile = File(...), actor: Actor = Depends(get_actor)):
        require_policy_manager(actor)
        data = await read_pdf_upload(request, file)
        doc = await run_in_threadpool(pipeline.add_company_doc, svc, data, file.filename or "document.pdf",
                                      actor.owner)
        note(actor, "uploaded_policy", doc.id, doc.filename, {"pages": doc.pages})
        return doc

    @app.get("/api/company/docs", response_model=list[CompanyDoc])
    def list_company_docs(actor: Actor = Depends(get_actor)):
        return svc.company.list(owner=actor.owner)

    @app.delete("/api/company/docs/{doc_id}", status_code=204)
    def delete_company_doc(doc_id: str, actor: Actor = Depends(get_actor)):
        require_policy_manager(actor)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id):
            raise UserError(404, "company_doc_not_found", "We could not find that document.")
        doc = pipeline.delete_company_doc(svc, doc_id, actor.owner)
        note(actor, "deleted_policy", doc.id, doc.filename)
        return Response(status_code=204)

    @app.get("/api/samples", response_model=list[Sample])
    def list_samples():
        return [Sample.model_validate(s) for s in load_samples()]

    @app.post("/api/reviews/sample/{sample_id}", response_model=ReviewResult)
    def review_sample(sample_id: str, request: Request, refresh: bool = False, actor: Actor = Depends(get_actor)):
        """Open a bundled sample in the caller's workspace. A demo visitor gets the shared demo copy.
        A signed-in user gets their own copy (copied from the demo one when it exists, no model call).

        Cached after the first run. ?refresh=1 re-runs it (counts against the rate limit); a demo visitor
        cannot refresh, because that would rewrite the shared demo workspace."""
        entry = next((s for s in load_samples() if s.get("id") == sample_id), None)
        if entry is None:
            raise UserError(404, "sample_not_found", "We could not find that sample contract.")
        with sample_locks[(actor.owner, sample_id)]:
            can_refresh = refresh and (actor.signed_in or actor.seeding)
            cached = None if can_refresh else svc.store.find_by_sample(sample_id, owner=actor.owner)
            if cached is not None:
                if audits_views(actor):
                    svc.audit.record_view(actor.audit_actor, cached.id, cached.filename, workspace_id=actor.owner)
                return cached
            path = sample_pdf_path(entry)
            if path is None:
                raise UserError(404, "sample_not_found", "That sample contract file is missing.")
            pdf = path.read_bytes()
            if actor.owner != DEMO_OWNER and not can_refresh:
                shared = svc.store.find_by_sample(sample_id, owner=DEMO_OWNER)
                if shared is not None:
                    result = pipeline.copy_review(svc, shared, pdf, actor.owner, actor.uid)
                    pipeline.audit_review(svc, actor.audit_actor, result, copied_from_sample=sample_id)
                    return result
            check_rate(request)
            result = pipeline.review(svc, pdf, path.name, sample_id=sample_id, owner=actor.owner,
                                    created_by=actor.uid)
            pipeline.audit_review(svc, actor.audit_actor, result, sample_id=sample_id)
            return result

    @app.get("/api/reviews", response_model=list[ReviewSummary])
    def list_reviews(limit: int = Query(50, ge=1, le=200), actor: Actor = Depends(get_actor)):
        return [ReviewSummary.of(r) for r in svc.store.list(owner=actor.owner, limit=limit)]

    @app.get("/api/reviews/{review_id}", response_model=ReviewResult)
    def get_review(review_id: str, actor: Actor = Depends(get_actor)):
        r = get_review_or_404(review_id, actor)
        if audits_views(actor):
            svc.audit.record_view(actor.audit_actor, r.id, r.filename, workspace_id=actor.owner)
        return r

    @app.get("/api/reviews/{review_id}/history", response_model=list[ReviewSummary])
    def review_history(review_id: str, actor: Actor = Depends(get_actor)):
        """Every run of the same document (same content hash) in this workspace, newest first."""
        return [ReviewSummary.of(r) for r in pipeline.history(svc, get_review_or_404(review_id, actor))]

    @app.delete("/api/reviews/{review_id}", status_code=204)
    def delete_review(review_id: str, actor: Actor = Depends(get_actor)):
        require_writer(actor)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", review_id):
            raise UserError(404, "review_not_found", "We could not find that review.")
        mine = get_review_or_404(review_id, actor)
        if not (actor.manages or actor.seeding or mine.created_by == actor.uid):
            raise UserError(403, "forbidden_role", "Members can delete only the reviews they ran. Ask an owner or admin.")
        r = pipeline.delete_review(svc, review_id, actor.owner)
        negotiation_cache.pop(review_id, None)
        note(actor, "deleted_review", r.id, r.filename)
        return Response(status_code=204)

    @app.get("/api/reviews/{review_id}/export")
    def export_review(review_id: str, actor: Actor = Depends(get_actor)):
        """The review as a JSON download."""
        r = get_review_or_404(review_id, actor)
        if audits_views(actor):
            note(actor, "exported", r.id, r.filename, {"format": "json"})
        return Response(r.model_dump_json(indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="review-{r.id}.json"'})

    @app.post("/api/reviews/{review_id}/negotiation", response_model=NegotiationDraft)
    def negotiate(review_id: str, actor: Actor = Depends(get_actor)):
        r = get_review_or_404(review_id, actor)
        if review_id not in negotiation_cache:  # one LLM call per review per instance
            negotiation_cache[review_id] = pipeline.negotiation(svc, r)
            note(actor, "drafted_reply", r.id, r.filename,
                             {"asks": len(negotiation_cache[review_id].asks)})
        return negotiation_cache[review_id]

    @app.get("/api/reviews/{review_id}/pdf")
    def get_pdf(review_id: str, actor: Actor = Depends(get_actor)):
        get_review_or_404(review_id, actor)
        data = svc.blobs.get(f"uploads/{review_id}.pdf")
        if data is None:
            raise UserError(404, "pdf_not_found", "The original PDF is no longer stored (uploads are kept 7 days).")
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{review_id}.pdf"'})

    @app.get("/api/search", response_model=list[SearchHit])
    def search(q: str = Query(..., min_length=1, max_length=500), limit: int = Query(10, ge=1, le=50),
               actor: Actor = Depends(get_actor)):
        return pipeline.search(svc, q.strip(), actor.owner, limit)

    # ---- knowledge library, playbook overrides, company policy PDF (app/routes_knowledge.py)
    app.include_router(routes_knowledge.router)

    # ---- company workspaces ------------------------------------------------------------------
    def need_user(actor: Actor) -> Identity:
        if actor.user is None:
            raise UserError(401, "sign_in_required", SIGN_IN_MESSAGE)
        return actor.user

    def membership(actor: Actor, workspace_id: str) -> tuple[Workspace, Member]:
        """The workspace and the caller's membership in it. Not a member (including a platform admin): 404."""
        user = need_user(actor)
        member = svc.workspaces.get_member(workspace_id, user.uid) if workspaces.WS_ID.fullmatch(workspace_id) else None
        ws = svc.workspaces.get(workspace_id) if member else None
        if ws is None or member is None:
            raise UserError(404, "workspace_not_found", "We could not find that workspace.")
        return workspaces.with_count(svc, ws), member

    @app.get("/api/workspaces", response_model=list[WorkspaceRef])
    def list_workspaces(actor: Actor = Depends(get_actor)):
        return workspaces.refs_for(svc, need_user(actor).uid)

    @app.get("/api/workspaces/{workspace_id}", response_model=Workspace)
    def get_workspace(workspace_id: str, actor: Actor = Depends(get_actor)):
        return membership(actor, workspace_id)[0]

    @app.patch("/api/workspaces/{workspace_id}", response_model=Workspace)
    def patch_workspace(workspace_id: str, body: WorkspacePatch, actor: Actor = Depends(get_actor)):
        """Owners and admins rename. Only an owner sets the auto-join domain, and only to the domain their own
        Google sign-in proves (`hd`): otherwise anyone could claim a company's domain and have its staff
        join their workspace."""
        ws, me_ = membership(actor, workspace_id)
        workspaces.require_role(me_.role, "owner", "admin", message="Only owners and admins can edit the workspace.")
        update: dict = {}
        if body.name is not None:
            name = body.name.strip()
            if not 1 <= len(name) <= 80:
                raise UserError(422, "invalid_request", "The workspace name must be 1 to 80 characters.")
            update["name"] = name
        if body.domain is not None:
            workspaces.require_role(me_.role, "owner", message="Only an owner can set the auto-join domain.")
            domain = body.domain.strip().lower()
            if ws.kind != "company" and domain:
                raise UserError(422, "invalid_request", "Only company workspaces can have an auto-join domain.")
            if domain and domain != (actor.user.hd or ""):  # type: ignore[union-attr]
                raise UserError(403, "domain_not_verified",
                                "You can only use the Google Workspace domain you are signed in with.")
            clash = svc.workspaces.find_by_domain(domain) if domain else None
            if clash is not None and clash.id != ws.id:
                raise UserError(409, "domain_taken", "Another workspace already uses that domain.")
            update["domain"] = domain or None
        if update:
            ws = ws.model_copy(update=update)
            svc.workspaces.put(ws)
        return ws

    @app.get("/api/workspaces/{workspace_id}/members", response_model=list[Member])
    def list_members(workspace_id: str, actor: Actor = Depends(get_actor)):
        membership(actor, workspace_id)
        return svc.workspaces.members(workspace_id)

    @app.patch("/api/workspaces/{workspace_id}/members/{uid}", response_model=Member)
    def change_role(workspace_id: str, uid: str, body: RoleBody, actor: Actor = Depends(get_actor)):
        """An owner sets any role. An admin may only move a plain member up to admin or back down."""
        _ws, me_ = membership(actor, workspace_id)
        workspaces.require_role(me_.role, "owner", "admin", message="Only owners and admins can change roles.")
        target = svc.workspaces.get_member(workspace_id, uid)
        if target is None:
            raise UserError(404, "member_not_found", "We could not find that member.")
        if me_.role == "admin" and (target.role == "owner" or body.role == "owner"):
            raise UserError(403, "forbidden_role", "Only an owner can make or change an owner.")
        if target.role == "owner" and body.role != "owner" and workspaces.count_owners(svc, workspace_id) <= 1:
            raise UserError(409, "last_owner", "A workspace needs at least one owner. Make someone else an owner first.")
        target = target.model_copy(update={"role": body.role})
        svc.workspaces.put_member(workspace_id, target)
        return target

    @app.delete("/api/workspaces/{workspace_id}/members/{uid}", status_code=204)
    def remove_member(workspace_id: str, uid: str, actor: Actor = Depends(get_actor)):
        """An owner removes anyone; an admin removes plain members; anyone may leave. The last owner cannot go."""
        _ws, me_ = membership(actor, workspace_id)
        target = svc.workspaces.get_member(workspace_id, uid)
        if target is None:
            raise UserError(404, "member_not_found", "We could not find that member.")
        if uid != actor.uid:
            workspaces.require_role(me_.role, "owner", "admin", message="Only owners and admins can remove members.")
            if me_.role == "admin" and target.role != "member":
                raise UserError(403, "forbidden_role", "Only an owner can remove an owner or an admin.")
        if target.role == "owner" and workspaces.count_owners(svc, workspace_id) <= 1:
            raise UserError(409, "last_owner", "A workspace needs at least one owner. Make someone else an owner first.")
        svc.workspaces.delete_member(workspace_id, uid)
        return Response(status_code=204)

    @app.post("/api/workspaces/{workspace_id}/invites", response_model=Invite)
    def create_invite(workspace_id: str, body: InviteBody | None = None, actor: Actor = Depends(get_actor)):
        """An invite code, valid 7 days, single use. Owners and admins invite; only an owner invites an admin."""
        _ws, me_ = membership(actor, workspace_id)
        workspaces.require_role(me_.role, "owner", "admin", message="Only owners and admins can invite people.")
        role = body.role if body else "member"
        if role == "admin":
            workspaces.require_role(me_.role, "owner", message="Only an owner can invite an admin.")
        return workspaces.create_invite(svc, workspace_id, role)

    @app.post("/api/invites/{code}/accept", response_model=WorkspaceRef)
    def accept_invite(code: str, actor: Actor = Depends(get_actor)):
        return workspaces.accept_invite(svc, code, need_user(actor))

    @app.get("/api/workspaces/{workspace_id}/overview")
    def workspace_overview(workspace_id: str, days: int = Query(30, ge=1, le=365), actor: Actor = Depends(get_actor)):
        """The dashboard for one workspace (same shape as the platform one). Owners and admins only."""
        _ws, me_ = membership(actor, workspace_id)
        workspaces.require_role(me_.role, "owner", "admin", message="Only owners and admins can see the dashboard.")
        return admin.overview(svc, days, owner=workspace_id)

    # ---- platform admin (ADMIN_EMAILS): aggregates and audit metadata only, never another user's contract text -----------
    @app.get("/api/admin/overview")
    def admin_overview(days: int = Query(30, ge=1, le=365), actor: Actor = Depends(get_actor)):
        require_admin(actor)
        return admin.overview(svc, days)

    @app.get("/api/admin/activity", response_model=list[AuditEvent])
    def admin_activity(limit: int = Query(200, ge=1, le=1000), actor: Actor = Depends(get_actor)):
        require_admin(actor)
        return admin.activity(svc, limit)

    @app.get("/api/admin/workspaces")
    def admin_workspaces(actor: Actor = Depends(get_actor)):
        require_admin(actor)
        return admin.workspaces_with_counts(svc)

    @app.get("/api/admin/users")
    def admin_users(actor: Actor = Depends(get_actor)):
        require_admin(actor)
        return admin.users_with_counts(svc)

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

