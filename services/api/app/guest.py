"""The read-only guest view of the demo company, so judges can see the signed-in product without a Google account.

A request with NO Authorization header and `X-Guest: demo` is a guest: workspace "demo", role "viewer" (below
member). A guest can read; it can never write, spend a model call, or reach any other workspace. The members shown
are invented people, never real users. No cloud SDK.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import DEMO_OWNER
from app.models import Member, Workspace
from app.pipeline import UserError

GUEST_HEADER = "x-guest"
GUEST_VALUE = "demo"
GUEST_ROLE = "viewer"
GUEST_MESSAGE = "This is a read-only demo. Sign in with Google to use your own workspace."

# Invented. example.com is reserved for documentation, so none of these can be a real address.
DEMO_MEMBERS: tuple[Member, ...] = (
    Member(uid="demo-owner", email="aisyah.rahman@example.com", name="Aisyah Rahman", role="owner",
           joined_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)),
    Member(uid="demo-admin", email="daniel.lim@example.com", name="Daniel Lim", role="admin",
           joined_at=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)),
    Member(uid="demo-member-1", email="priya.nair@example.com", name="Priya Nair", role="member",
           joined_at=datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)),
    Member(uid="demo-member-2", email="hafiz.omar@example.com", name="Hafiz Omar", role="member",
           joined_at=datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)),
)

DEMO_WORKSPACE = Workspace(id=DEMO_OWNER, name="Kopi Kita Sdn Bhd", kind="demo", domain=None,
                           created_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc), member_count=len(DEMO_MEMBERS))


def guest_forbidden() -> UserError:
    return UserError(403, "guest_read_only", GUEST_MESSAGE)


def gate(workspace_id: str) -> None:
    """A guest reaches the demo workspace and nothing else: any other id is 404, exactly like a missing one."""
    if workspace_id != DEMO_OWNER:
        raise UserError(404, "workspace_not_found", "We could not find that workspace.")


def block_write(workspace_id: str | None = None) -> None:
    """For a write route called by a guest: 404 for another workspace's id, else 403 guest_read_only."""
    if workspace_id is not None:
        gate(workspace_id)
    raise guest_forbidden()
