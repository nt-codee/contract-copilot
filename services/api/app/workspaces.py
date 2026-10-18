"""Company workspaces: who belongs where, who may do what, and how a new user lands in one.

Tenancy is per workspace: a review, policy, chunk or audit event belongs to a workspace id, and every
read is filtered by it. No cloud SDK.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from app.models import Identity, Invite, Member, Role, User, Workspace, WorkspaceRef
from app.pipeline import Services, UserError

INVITE_DAYS = 7
ROLE_RANK = {"member": 0, "admin": 1, "owner": 2}
WS_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def company_ws_id(domain: str) -> str:
    """Deterministic, so two first sign-ins from one new domain cannot create two workspaces."""
    return "co-" + hashlib.sha256(domain.lower().encode()).hexdigest()[:12]


def personal_ws_id(uid: str) -> str:
    return "p-" + hashlib.sha256(uid.encode()).hexdigest()[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _member(ident: Identity, role: Role) -> Member:
    return Member(uid=ident.uid, email=ident.email, name=ident.name, role=role, joined_at=_now())


def provision(svc: Services, ident: Identity, *, first_sign_in: bool) -> None:
    """Give a user with no workspace one.

    On a FIRST sign-in only, a Google Workspace user (verified email and an `hd` claim) joins the company
    workspace of that domain as a member, or founds it as its owner when none exists. Everyone else, and
    anyone who was removed from a workspace and has none left, gets a personal workspace. Auto-join is not
    repeated after a removal, or removing someone would be pointless.
    """
    hd = (ident.hd or "").strip().lower() if ident.email_verified else ""
    ws = svc.workspaces
    if hd and first_sign_in:
        existing = ws.find_by_domain(hd)
        if existing is None:
            founded = Workspace(id=company_ws_id(hd), name=hd, kind="company", domain=hd, created_at=_now())
            if ws.create(founded):
                ws.put_member(founded.id, _member(ident, "owner"))
                return
            existing = ws.get(founded.id)  # a concurrent first sign-in founded it
        if existing is not None:
            if ws.get_member(existing.id, ident.uid) is None:
                ws.put_member(existing.id, _member(ident, "member"))
            return
    personal = Workspace(id=personal_ws_id(ident.uid), name=f"{ident.name or ident.email}'s workspace",
                         kind="personal", created_at=_now())
    ws.create(personal)  # False when it already exists: then just make sure the owner is in it
    if ws.get_member(personal.id, ident.uid) is None:
        ws.put_member(personal.id, _member(ident, "owner"))


def with_count(svc: Services, w: Workspace) -> Workspace:
    return w.model_copy(update={"member_count": len(svc.workspaces.members(w.id))})


def refs_for(svc: Services, uid: str) -> list[WorkspaceRef]:
    return [WorkspaceRef(workspace=with_count(svc, w), role=m.role) for w, m in svc.workspaces.memberships(uid)]


def require_role(role: str | None, *allowed: str, message: str) -> None:
    if role not in allowed:
        raise UserError(403, "forbidden_role", message)


def count_owners(svc: Services, workspace_id: str) -> int:
    return sum(1 for m in svc.workspaces.members(workspace_id) if m.role == "owner")


def create_invite(svc: Services, workspace_id: str, role: str) -> Invite:
    inv = Invite(code=secrets.token_urlsafe(18), workspace_id=workspace_id, role=role,  # type: ignore[arg-type]
                 expires_at=_now() + timedelta(days=INVITE_DAYS))
    svc.workspaces.put_invite(inv)
    return inv


def accept_invite(svc: Services, code: str, ident: Identity) -> WorkspaceRef:
    """Join the invited workspace with the invited role. A code is single-use and expires after 7 days."""
    inv = svc.workspaces.get_invite(code) if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", code) else None
    if inv is None:
        raise UserError(404, "invite_not_found", "That invite link is not valid.")
    ws = svc.workspaces.get(inv.workspace_id)
    if inv.expires_at is not None and inv.expires_at < _now() or ws is None:
        svc.workspaces.delete_invite(code)
        raise UserError(410, "invite_expired", "That invite has expired. Ask for a new one.")
    current = svc.workspaces.get_member(ws.id, ident.uid)
    if current is None:
        svc.workspaces.put_member(ws.id, _member(ident, inv.role))
        current = svc.workspaces.get_member(ws.id, ident.uid)
    svc.workspaces.delete_invite(code)
    return WorkspaceRef(workspace=with_count(svc, ws), role=current.role)  # type: ignore[union-attr]


def user_view(svc: Services, ident: Identity, platform_admin: bool, stored: User | None) -> User:
    return User(uid=ident.uid, email=ident.email, name=ident.name, photo_url=ident.photo_url,
                created_at=stored.created_at if stored else None,
                last_seen_at=stored.last_seen_at if stored else None,
                workspaces=refs_for(svc, ident.uid), platform_admin=platform_admin)
