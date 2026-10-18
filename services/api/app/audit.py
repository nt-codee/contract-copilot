"""The audit trail: who did what, when, to which thing. Append-only.

Events hold metadata only (ids, filenames, model and version strings, counts), never contract text.
There is no update path. The only delete is `forget`, used by a user's own 'delete my data'.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from app.models import AuditEvent
from app.ports import AuditLog

log = logging.getLogger("cc.audit")

VIEW_DEDUPE_S = 60.0  # re-opening the same review inside this window is one view, not many


class Audit:
    def __init__(self, log_: AuditLog):
        self._log = log_
        self._recent: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def record(self, actor: str, action: str, target_id: str | None = None,
               target_label: str | None = None, detail: dict | None = None, *,
               workspace_id: str | None = None) -> AuditEvent:
        """Write one event. An unknown `action` is a programming error and raises.

        A storage failure is logged at ERROR and swallowed: losing the trail line is better than losing
        the review the user just waited a minute for.
        """
        event = AuditEvent(id=uuid.uuid4().hex, at=datetime.now(timezone.utc), actor=actor, action=action,  # type: ignore[arg-type]
                           target_id=target_id, target_label=target_label, detail=detail or None,
                           workspace_id=workspace_id)
        try:
            self._log.append(event)
        except Exception:
            log.exception("AUDIT WRITE FAILED actor=%s action=%s target=%s", actor, action, target_id)
        return event

    def record_view(self, actor: str, target_id: str, target_label: str | None, *,
                    workspace_id: str | None = None) -> None:
        """`viewed_review`, at most once a minute per actor and review."""
        key, now = (actor, target_id), time.monotonic()
        with self._lock:
            if now - self._recent.get(key, -VIEW_DEDUPE_S) < VIEW_DEDUPE_S:
                return
            self._recent[key] = now
            if len(self._recent) > 5000:
                self._recent = {k: t for k, t in self._recent.items() if now - t < VIEW_DEDUPE_S}
        self.record(actor, "viewed_review", target_id, target_label, workspace_id=workspace_id)

    def for_workspace(self, workspace_id: str, limit: int, *, actor: str | None = None) -> list[AuditEvent]:
        return self._log.list_for(workspace_id, limit, actor=actor)

    def everyone(self, limit: int) -> list[AuditEvent]:
        return self._log.list_all(limit)

    def forget(self, actor: str) -> int:
        """Delete one user's own events. Only for 'delete my data'."""
        return self._log.delete_actor(actor)

    def forget_workspace(self, workspace_id: str) -> int:
        """Delete a workspace's events. Only when its last member deletes their data."""
        return self._log.delete_workspace(workspace_id)


def now() -> datetime:
    return datetime.now(timezone.utc)
