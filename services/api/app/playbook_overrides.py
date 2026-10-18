"""Workspace playbook overrides: "your standard positions", the user's steering of the review.

The defaults (knowledge/playbook) stay read-only. An override is stored per (owner, contract type,
clause type); `owner` is the active workspace id ("demo" for the public demo workspace).
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field

from app.knowledge import PlaybookEntry, Knowledge, _section, clip, sections

log = logging.getLogger("cc.playbook")

SUFFIX = " (your company's position)"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class PlaybookOverrideIn(BaseModel):
    standard: str = Field(default="", max_length=4000, description="what a balanced version says, in your words")
    red_flags: list[str] = Field(default_factory=list, max_length=12)
    ask: str = Field(default="", max_length=1000, description="what to ask the other side for")
    note: str = Field(default="", max_length=2000)

    def cleaned(self) -> "PlaybookOverrideIn":
        flags = [f.strip()[:500] for f in self.red_flags if f.strip()]
        return PlaybookOverrideIn(standard=self.standard.strip(), red_flags=flags, ask=self.ask.strip(),
                                  note=self.note.strip())


class PlaybookOverride(PlaybookOverrideIn):
    updated_by: str
    updated_at: datetime


class PlaybookItem(BaseModel):
    """One row of "your standard positions": the default, or this owner's override of it."""

    id: str  # playbook:<type>/<clause>, or playbook:<owner>/<type>/<clause> when overridden
    contract_type: str
    clause_type: str
    title: str
    standard: str
    red_flags: list[str] = Field(default_factory=list)
    ask: str = ""
    note: str = ""
    overridden: bool = False
    updated_by: str | None = None
    updated_at: datetime | None = None


class PlaybookOverrideStore(Protocol):
    def get(self, owner: str, contract_type: str, clause_type: str) -> PlaybookOverride | None: ...
    def list(self, owner: str) -> dict[tuple[str, str], PlaybookOverride]: ...
    def put(self, owner: str, contract_type: str, clause_type: str, body: PlaybookOverrideIn,
            updated_by: str) -> PlaybookOverride: ...
    def delete(self, owner: str, contract_type: str, clause_type: str) -> bool: ...


class MemoryPlaybookOverrides:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], PlaybookOverride] = {}
        self._lock = threading.Lock()

    def get(self, owner, contract_type, clause_type):
        with self._lock:
            return self._items.get((owner, contract_type, clause_type))

    def list(self, owner):
        with self._lock:
            return {(ct, cl): v for (o, ct, cl), v in self._items.items() if o == owner}

    def put(self, owner, contract_type, clause_type, body, updated_by):
        item = PlaybookOverride(**body.cleaned().model_dump(), updated_by=updated_by,
                                updated_at=datetime.now(timezone.utc))
        with self._lock:
            self._items[(owner, contract_type, clause_type)] = item
        return item

    def delete(self, owner, contract_type, clause_type):
        with self._lock:
            return self._items.pop((owner, contract_type, clause_type), None) is not None


def _bullets(text: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in text.splitlines() if ln.strip().startswith("- ")]


def default_item(e: PlaybookEntry) -> PlaybookItem:
    ctype, clause = e.ref.split("/", 1)
    secs = sections(e.text)
    return PlaybookItem(id=f"playbook:{e.ref}", contract_type=ctype, clause_type=clause, title=e.title,
                        standard=e.balanced, red_flags=_bullets(_section(secs, "red flags")),
                        ask=_section(secs, "suggested ask"))


def merged_items(kb: Knowledge, store: PlaybookOverrideStore | None, owner: str,
                 contract_type: str | None = None) -> list[PlaybookItem]:
    """Defaults, with this owner's overrides in place; overrides that have no default are appended."""
    mine = store.list(owner) if store else {}
    items: list[PlaybookItem] = []
    seen: set[tuple[str, str]] = set()
    for ref, e in sorted(kb.playbook.items()):
        ctype, clause = ref.split("/", 1)
        if contract_type and ctype != contract_type:
            continue
        seen.add((ctype, clause))
        base = default_item(e)
        ov = mine.get((ctype, clause))
        items.append(_overridden(base, owner, ov) if ov else base)
    for (ctype, clause), ov in sorted(mine.items()):
        if (ctype, clause) not in seen and (not contract_type or ctype == contract_type):
            title = f"{clause.replace('_', ' ').capitalize()} ({ctype.replace('_', ' ')})"
            items.append(_overridden(PlaybookItem(id="", contract_type=ctype, clause_type=clause, title=title,
                                                  standard=""), owner, ov))
    return items


def _overridden(base: PlaybookItem, owner: str, ov: PlaybookOverride) -> PlaybookItem:
    return base.model_copy(update={
        "id": f"playbook:{owner}/{base.contract_type}/{base.clause_type}", "standard": ov.standard,
        "red_flags": ov.red_flags, "ask": ov.ask, "note": ov.note, "overridden": True,
        "updated_by": ov.updated_by, "updated_at": ov.updated_at})


def override_ref(owner: str, contract_type: str, clause_type: str) -> str:
    return f"playbook:{owner}/{contract_type}/{clause_type}"


def override_excerpt(ov: PlaybookOverride, limit: int) -> str:
    """What the agent reads: the company's own wording, labelled."""
    parts = [f"Company standard: {ov.standard}" if ov.standard else ""]
    if ov.red_flags:
        parts.append("Company red flags: " + "; ".join(ov.red_flags))
    if ov.ask:
        parts.append(f"Company ask: {ov.ask}")
    if ov.note:
        parts.append(f"Note: {ov.note}")
    return clip(" ".join(p for p in parts if p), limit)


# Audit actions for playbook edits. AuditEvent.action is a closed enum in shared/contracts; until these two
# values are added there (and to models.AuditAction) the edit is logged but not written to the trail.
AUDIT_SET, AUDIT_REVERT = "edited_standard", "reverted_standard"


def audit_record(audit, action: str, *, actor: str, workspace_id: str, target_id: str, label: str | None = None,
                 detail: dict | None = None) -> bool:
    """Write one line to the audit trail (app.audit.Audit.record). False when the action is not (yet) allowed."""
    from typing import get_args

    from app.models import AuditAction

    if action not in get_args(AuditAction):
        log.warning("audit action %r is not in the schema enum; playbook edit by %s not written to the trail",
                    action, actor)
        return False
    audit.record(actor, action, target_id, label, detail, workspace_id=workspace_id)
    return True
