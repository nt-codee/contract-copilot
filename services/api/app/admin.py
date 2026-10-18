"""Aggregates for the admin dashboard. Metadata only: counts, risk levels, timings, costs, sources.

`owner=None` aggregates every workspace (platform admin); a workspace id scopes it to that workspace.
Nothing here returns a clause, a quote, a party name or a filename from anyone's contract; an admin
sees how the service is used, not what users uploaded. No cloud SDK.
"""

from __future__ import annotations

import math
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from app.models import AuditEvent, User
from app.pipeline import Services

SCAN_CAP = 2000  # reviews read per call
CACHE_S = 60.0
TOP_N = 10
DEADLINE_WINDOW_DAYS = 30
MAX_DEADLINES = 20

_lock = threading.Lock()


def percentile(values: list[int | float], p: float) -> int:
    """Nearest-rank percentile; 0 for no data."""
    if not values:
        return 0
    ordered = sorted(values)
    return int(ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)])


def overview(svc: Services, days: int = 30, today: date | None = None, use_cache: bool = True,
             owner: str | None = None) -> dict:
    """Usage over the last `days` days, from a capped scan (<= SCAN_CAP newest reviews). Cached 60 s."""
    now_m = time.monotonic()
    with _lock:
        cache: dict[tuple, tuple[float, dict]] = svc.__dict__.setdefault("_admin_cache", {})  # per service set
        hit = cache.get((days, owner)) if use_cache else None
        if hit and now_m - hit[0] < CACHE_S:
            return hit[1]
    result = _compute(svc, days, today, owner)
    with _lock:
        cache[(days, owner)] = (now_m, result)
    return result


def _compute(svc: Services, days: int, today: date | None, owner: str | None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    since = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), tzinfo=timezone.utc)

    by_day: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_risk: Counter[str] = Counter()
    clause: dict[str, Counter[str]] = defaultdict(Counter)
    breaches: Counter[str] = Counter()
    cite_kinds: Counter[str] = Counter()
    sources: dict[str, dict] = {}
    latencies: list[int] = []
    total_cost = 0.0
    tokens_in = tokens_out = 0
    dropped_q = dropped_c = 0
    deadlines: list[dict] = []
    n = 0
    horizon = today + timedelta(days=DEADLINE_WINDOW_DAYS)

    for r in svc.store.scan(limit=SCAN_CAP, since=since, owner=owner):  # streamed: only counters are kept
        n += 1
        by_day[r.created_at.astimezone(timezone.utc).date().isoformat()] += 1
        by_type[r.contract_type] += 1
        by_risk[r.verdict.overall_risk] += 1
        m = r.metrics
        dropped_q += m.dropped_unverified
        dropped_c += m.dropped_unverified_citations
        if (m.input_tokens or 0) > 0 or m.input_tokens is None:  # a model run, not a copy of one
            latencies.append(m.latency_ms)
            total_cost += m.cost_usd_est or 0.0
            tokens_in += m.input_tokens or 0
            tokens_out += m.output_tokens or 0
        for f in r.findings:
            clause[f.clause_type][f.risk] += 1
            for c in f.citations:
                cite_kinds[c.kind] += 1
                if c.kind == "company":
                    breaches[c.title] += 1 if f.policy_breach else 0
                else:  # a company document is a user's private file: counted above, never listed
                    s = sources.setdefault(c.ref, {"ref": c.ref, "title": c.title, "count": 0})
                    s["count"] += 1
        for k in r.key_dates:
            if k.date and k.kind in ("notice_deadline", "renewal") and today <= k.date <= horizon:
                deadlines.append({"date": k.date.isoformat(), "kind": k.kind, "contract_type": r.contract_type,
                                  "days_left": (k.date - today).days})

    series = [{"date": (since.date() + timedelta(days=i)).isoformat(),
               "count": by_day.get((since.date() + timedelta(days=i)).isoformat(), 0)} for i in range(days)]
    top_clauses = sorted(({"clause_type": t, "high": c["high"], "medium": c["medium"], "low": c["low"]}
                          for t, c in clause.items()),
                         key=lambda x: (-(x["high"] + x["medium"] + x["low"]), -x["high"], x["clause_type"]))[:TOP_N]
    deadlines.sort(key=lambda d: (d["date"], d["kind"]))
    return {
        "users": len(svc.workspaces.members(owner)) if owner else len(svc.users.list()),
        "reviews": n,
        "reviews_by_day": series,
        "by_contract_type": dict(by_type),
        "by_risk": {k: by_risk.get(k, 0) for k in ("high", "medium", "low")},
        "top_clause_types": top_clauses,
        "policy_breaches_by_rule": [{"rule": t, "count": c} for t, c in breaches.most_common(TOP_N) if c],
        "citations_by_kind": {k: cite_kinds.get(k, 0) for k in ("law", "case", "playbook", "company")},
        "top_cited_sources": sorted(sources.values(), key=lambda s: (-s["count"], s["ref"]))[:TOP_N],
        "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95),
                       "max": max(latencies, default=0)},
        "cost_usd": {"total": round(total_cost, 6),
                     "per_review_mean": round(total_cost / len(latencies), 6) if latencies else 0.0},
        "tokens": {"in": tokens_in, "out": tokens_out},
        "dropped_unverified": {"quotes": dropped_q, "citations": dropped_c},
        "upcoming_deadlines": deadlines[:MAX_DEADLINES],
    }


def users_with_counts(svc: Services) -> list[dict]:
    """Every user with the number of reviews they ran (capped scan), their workspaces, and last_seen_at."""
    counts: Counter[str] = Counter()
    for r in svc.store.scan(limit=SCAN_CAP):
        if r.created_by:
            counts[r.created_by] += 1
    users: list[User] = svc.users.list()
    rows = []
    for u in users:
        mine = svc.workspaces.memberships(u.uid)
        rows.append({**u.model_dump(mode="json", exclude={"workspaces", "platform_admin"}),
                     "review_count": counts.get(u.uid, 0),
                     "workspaces": [{"id": w.id, "name": w.name, "role": m.role} for w, m in mine]})
    rows.sort(key=lambda r: r.get("last_seen_at") or "", reverse=True)
    return rows


def workspaces_with_counts(svc: Services) -> list[dict]:
    """Every workspace with member and review counts. No contract content."""
    counts: Counter[str] = Counter()
    last: dict[str, datetime] = {}
    for r in svc.store.scan(limit=SCAN_CAP):
        if r.owner:
            counts[r.owner] += 1
            last[r.owner] = max(last.get(r.owner, r.created_at), r.created_at)
    rows = []
    for w in svc.workspaces.list_all():
        rows.append({**w.model_dump(mode="json"), "member_count": len(svc.workspaces.members(w.id)),
                     "review_count": counts.get(w.id, 0),
                     "last_review_at": last[w.id].isoformat() if w.id in last else None})
    rows.sort(key=lambda r: (-r["review_count"], r["name"]))
    return rows


def activity(svc: Services, limit: int) -> list[AuditEvent]:
    """Every workspace's audit events (metadata only)."""
    return svc.audit.everyone(limit)
