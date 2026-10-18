"""A global daily cap on fresh model reviews, so the public URL can never burn the trial credit.

Counted across all instances (one counter per UTC day, incremented atomically in the store), for reviews that spend
model calls: uploads and fresh sample runs. Copies of an already-reviewed sample, reply drafts and policy uploads are
not counted. Platform admins are exempt (the caller checks). No cloud SDK.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.pipeline import UserError
from app.ports import SpendCounter, UpstreamError

log = logging.getLogger("cc.spend")

DAILY_LIMIT_MESSAGE = "The demo has reached today's review limit. Please try again tomorrow, or browse the sample workspace."


def today() -> str:
    """The UTC day the counter is kept for."""
    return datetime.now(timezone.utc).date().isoformat()


def next_count(current: int, cap: int) -> int | None:
    """The counter's new value, or None when the day's cap is already reached (pure, so the store's transaction
    and the in-memory counter share one rule)."""
    return current + 1 if current < cap else None


def charge(counter: SpendCounter | None, cap: int) -> None:
    """Take one review from today's allowance, or raise 503 `daily_limit`. A counter that cannot be reached fails
    CLOSED (a safe 502, no model call): a guard that fails open is not a guard."""
    if counter is None:
        return
    try:
        allowed = counter.take(f"reviews-{today()}", cap)
    except Exception:
        log.exception("spend counter unavailable; refusing the review")
        raise UpstreamError("The demo is temporarily unavailable. Please try again in a few minutes.") from None
    if not allowed:
        log.warning("daily review limit reached (%d)", cap)
        raise UserError(503, "daily_limit", DAILY_LIMIT_MESSAGE)
