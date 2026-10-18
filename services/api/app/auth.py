"""Who is calling, and which workspace they work in.

No Authorization header is the public demo visitor (never a login wall). A present but invalid or
expired token is 401. Verification itself sits behind the AuthVerifier port. A signed-in user works in
one workspace at a time: `X-Workspace-Id` picks it (membership is checked on every request), the
default is their first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Request

from app import workspaces
from app.config import DEMO_OWNER, Settings
from app.models import Identity, User, Workspace
from app.pipeline import UserError
from app.ports import InvalidToken

SIGN_IN_MESSAGE = "Sign in with Google to review your own contract."
WORKSPACE_HEADER = "x-workspace-id"


def is_admin(user: Identity | None, settings: Settings) -> bool:
    """Platform admin: the user's verified email is listed in ADMIN_EMAILS."""
    return bool(user and user.email_verified and user.email.strip().lower() in settings.admin_emails)


def current_user(request: Request) -> Identity | None:
    """FastAPI dependency. None for a demo visitor; 401 for a bad token."""
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise UserError(401, "invalid_token", "Your sign-in is not valid. Please sign in again.")
    try:
        user = request.app.state.services.auth.verify(token)
    except InvalidToken:
        raise UserError(401, "invalid_token",
                        "Your sign-in has expired or is not valid. Please sign in again.") from None
    if user.uid == DEMO_OWNER:  # the demo workspace id can never be a user's
        raise UserError(401, "invalid_token", "Your sign-in is not valid. Please sign in again.")
    return user


@dataclass(frozen=True)
class Actor:
    """The caller, and the workspace this request reads and writes."""

    user: Identity | None
    owner: str  # the active workspace id, or "demo"
    role: str | None = None  # owner | admin | member in the active workspace; None in the demo workspace
    admin: bool = False  # platform admin (ADMIN_EMAILS)
    seeding: bool = False  # may write to the demo workspace (seed_demo.py)

    @property
    def signed_in(self) -> bool:
        return self.user is not None

    @property
    def uid(self) -> str | None:
        return self.user.uid if self.user else None

    @property
    def audit_actor(self) -> str:
        """Who an audit line names: the uid, or "demo" in the public demo workspace."""
        return self.user.uid if self.user and not self.seeding else DEMO_OWNER

    @property
    def manages(self) -> bool:
        return self.role in ("owner", "admin")


def get_actor(request: Request) -> Actor:
    """Resolve the caller and the active workspace.

    `X-Workspace-Id: demo` selects the demo workspace for writing and is honoured only for a platform
    admin, or for an anonymous caller when CLOUD=local (offline seeding); anyone else gets 403. Any other
    id must be a workspace the user belongs to (403 otherwise)."""
    svc = request.app.state.services
    settings: Settings = svc.settings
    user = current_user(request)
    admin = is_admin(user, settings)
    wanted = request.headers.get(WORKSPACE_HEADER, "").strip()
    if wanted == DEMO_OWNER:
        if admin or (user is None and settings.cloud == "local"):
            return Actor(user, DEMO_OWNER, None, admin, seeding=True)
        raise UserError(403, "demo_read_only", "The demo workspace is read-only.")
    if user is None:
        return Actor(None, DEMO_OWNER)
    if wanted:
        member = svc.workspaces.get_member(wanted, user.uid) if workspaces.WS_ID.fullmatch(wanted) else None
        if member is None:
            raise UserError(403, "not_a_member", "You are not a member of that workspace.")
        return Actor(user, wanted, member.role, admin)
    memberships = svc.workspaces.memberships(user.uid)
    if not memberships:
        first = svc.users.get(user.uid) is None
        workspaces.provision(svc, user, first_sign_in=first)
        if first:
            now = datetime.now(timezone.utc)
            svc.users.put(User(uid=user.uid, email=user.email, name=user.name, photo_url=user.photo_url,
                               created_at=now, last_seen_at=None))  # None: /api/me records the sign-in
        memberships = svc.workspaces.memberships(user.uid)
    ws, member = memberships[0]
    return Actor(user, ws.id, member.role, admin)


def active_workspace(request: Request, actor: Actor) -> Workspace | None:
    return request.app.state.services.workspaces.get(actor.owner)


def require_writer(actor: Actor) -> None:
    """Upload a contract: any member of a workspace, or the seeder in the demo one."""
    if actor.user is None and not actor.seeding:
        raise UserError(401, "sign_in_required", SIGN_IN_MESSAGE)


def require_policy_manager(actor: Actor) -> None:
    """Upload or delete company policies: workspace owners and admins (and the seeder)."""
    require_writer(actor)
    if not actor.seeding and not actor.manages:
        raise UserError(403, "forbidden_role", "Only workspace owners and admins can change company policies.")


def require_admin(actor: Actor) -> None:
    if not actor.admin:  # a demo visitor gets the same 403 as a signed-in non-admin
        raise UserError(403, "forbidden", "This page is for administrators.")
