"""Knowledge API, key dates and the demo seed script."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from datetime import date

import pytest
import uvicorn

from app.config import REPO_ROOT
from app.models import KeyDate, next_deadline
from app.pipeline import add_months, build_key_dates, compute_date
from app.models import LLMKeyDate
from conftest import ROOT

# ---- knowledge API -------------------------------------------------------------------------------


def test_knowledge_list_filters_and_has_no_text(client):
    allv = client.get("/api/knowledge").json()
    kinds = {e["kind"] for e in allv}
    assert kinds == {"law", "playbook"}  # fixtures hold statutes only; the real playbook is loaded
    assert all(e["text"] is None for e in allv)
    assert sum(e["kind"] == "playbook" for e in allv) >= 22
    my = client.get("/api/knowledge", params={"jurisdiction": "my"}).json()
    assert {e["id"] for e in my} == {"my-contracts-act-s75", "my-contracts-act-s28"}
    nda = client.get("/api/knowledge", params={"kind": "playbook", "contract_type": "nda"}).json()
    assert nda and all(e["contract_type"] == "nda" and e["id"].startswith("playbook:nda/") for e in nda)
    hit = client.get("/api/knowledge", params={"q": "penalty liquidated"}).json()
    assert [e["id"] for e in hit][0] == "my-contracts-act-s75"
    assert client.get("/api/knowledge", params={"q": "zzzzqqq"}).json() == []
    assert client.get("/api/knowledge", params={"kind": "bogus"}).status_code == 422


def test_knowledge_detail_has_text_and_url(client):
    e = client.get("/api/knowledge/my-contracts-act-s75").json()
    assert e["title"] == "Contracts Act 1950, s.75" and e["url"] == "https://example.test/my/contracts-act-s75"
    assert "reasonable compensation" in e["text"] and e["plain"]
    p = client.get("/api/knowledge/playbook:service_agreement/liability").json()
    assert p["kind"] == "playbook" and p["contract_type"] == "service_agreement"
    assert p["clause_types"] == ["liability"] and p["text"] and p["url"] is None
    assert client.get("/api/knowledge/playbook:nda/nope").status_code == 404
    assert client.get("/api/knowledge/not-an-id").json()["error"]["code"] == "knowledge_not_found"


def test_knowledge_against_the_real_library():
    from app.knowledge import Knowledge

    kb = Knowledge.load(REPO_ROOT / "knowledge")
    law = json.loads((REPO_ROOT / "knowledge" / "law" / "index.json").read_text(encoding="utf-8"))
    loaded = {e.id for e in kb.entries() if e.kind in ("law", "case")}
    assert len(loaded) >= 32
    assert {e["id"] for e in law} <= loaded  # every index entry loads
    assert sum(e.kind == "playbook" for e in kb.entries()) >= 22


# ---- key dates -----------------------------------------------------------------------------------


def test_notice_deadline_from_effective_date_plus_term():
    # 12-month term starting 1 Jan 2026 ends 31 Dec 2026; 90 days before that is 2 Oct 2026
    assert compute_date("2026-01-01", 12, -1) == date(2026, 12, 31)
    assert compute_date("2026-01-01", 12, -1 - 90) == date(2026, 10, 2)


def test_date_is_none_without_a_usable_base():
    assert compute_date("", 12, -91) is None
    assert compute_date("not a date", 12, 0) is None
    assert compute_date("2026-13-40", 1, 0) is None


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert add_months(date(2026, 12, 1), 12) == date(2027, 12, 1)


def _llm(kind, base, months, days, page=1, title=""):
    return LLMKeyDate(label=f"{kind} date", kind=kind, page=page, base_date=base, months=months, days=days,
                      rule="r", finding_title=title)


def test_build_key_dates_orders_links_and_drops_bad_pages():
    from app.models import Finding

    f = Finding(id="f2", clause_type="auto_renewal", title="Auto renewal", risk="medium", quote="q" * 20, page=2,
                why_risky="w", standard="s", suggested_ask="a")
    out = build_key_dates([
        _llm("end", "2026-01-01", 12, -1, 2), _llm("start", "", 0, 0, 1),
        _llm("notice_deadline", "2026-01-01", 12, -91, 2, "auto RENEWAL"), _llm("other", "2026-01-01", 0, 0, 9),
    ], [f], page_count=3)
    assert [(k.kind, k.date) for k in out] == [
        ("notice_deadline", date(2026, 10, 2)), ("end", date(2026, 12, 31)), ("start", None)]
    assert out[0].finding_id == "f2" and out[1].finding_id is None


def _kd(kind, d):
    return KeyDate(label=kind, kind=kind, date=d, page=1)


def test_next_deadline_selection():
    today = date(2026, 6, 1)
    kds = [_kd("end", date(2026, 7, 1)), _kd("notice_deadline", date(2026, 10, 2)),
           _kd("renewal", date(2027, 1, 1)), _kd("notice_deadline", date(2026, 3, 1)), _kd("start", None)]
    assert next_deadline(kds, today).date == date(2026, 10, 2)  # earliest notice/renewal on or after today
    past = [_kd("notice_deadline", date(2026, 3, 1)), _kd("end", date(2026, 2, 1)), _kd("start", None)]
    assert next_deadline(past, today).date == date(2026, 2, 1)  # nothing upcoming: earliest dated one
    assert next_deadline([_kd("start", None)], today) is None and next_deadline([], today) is None
    assert next_deadline([_kd("notice_deadline", today)], today).date == today  # today counts


def test_review_carries_key_dates_and_summary_next_deadline(client, pdf_bytes):
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    kd = {k["kind"]: k for k in body["key_dates"]}
    assert set(kd) == {"notice_deadline", "start"}  # the page-99 entry was dropped
    assert kd["notice_deadline"]["date"] == "2026-10-02" and kd["notice_deadline"]["page"] == 2
    assert kd["notice_deadline"]["finding_id"] == "f3" and kd["start"]["date"] is None
    assert kd["start"]["rule"]
    summary = client.get("/api/reviews").json()[0]
    assert summary["next_deadline"]["kind"] == "notice_deadline"  # fixture date is in the past: earliest dated


# ---- seed script ----------------------------------------------------------------------------------


@pytest.fixture
def live_server(services):
    from app.main import create_app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(services.settings, services), host="127.0.0.1",
                                           port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


def test_seed_demo_is_idempotent(live_server, services, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import seed_demo

    assert seed_demo.main(["--base-url", live_server, "--token", ROOT, "--pause", "0"]) == 0
    out = capsys.readouterr().out
    assert "kopi-kita-contracting-policy.pdf: uploaded" in out
    row = next(line for line in out.splitlines() if line.startswith("demo-01"))
    assert row.split()[1] == "ok"
    assert [d.filename for d in services.company.list(owner="demo")] == ["kopi-kita-contracting-policy.pdf"]
    n_calls = len(services.llm.calls)

    assert seed_demo.main(["--base-url", live_server, "--token", ROOT, "--pause", "0"]) == 0  # again: nothing re-uploaded
    out2 = capsys.readouterr().out
    assert "already there" in out2 and len(services.company.list(owner="demo")) == 1
    assert len(services.llm.calls) == n_calls  # cached sample review, no model call

    assert seed_demo.main(["--base-url", live_server, "--token", ROOT, "--pause", "0", "--refresh"]) == 0
    assert len(services.llm.calls) > n_calls  # --refresh re-runs it
