"""Account/day atomic reservations and metered provider attempts, with no network."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
import threading

import pytest
from google.genai import types
from pydantic import BaseModel

from app import budget
from app.adapters.local import MemoryBudgetLedger
from app.adapters.gcp_budget import FirestoreBudgetLedger
from app.adapters.gcp import GeminiLLM, GeminiEmbedder
from app.budget import AccountBudget, BudgetError, cost_micros, request_services
from app.ports import BudgetReservation, ChatMessage, PdfPart, ToolSpec, UpstreamError


class Out(BaseModel):
    ok: bool = True


class Failure(Exception):
    def __init__(self, code):
        self.code = code


class Models:
    def __init__(self, script=None, usage=True):
        self.script, self.usage = list(script or []), usage
        self.counts, self.generations, self.embeddings = [], [], []
        self.lock = threading.Lock()

    def count_tokens(self, **kwargs):
        self.counts.append(kwargs)
        return NS(total_tokens=200)

    def generate_content(self, **kwargs):
        self.generations.append(kwargs)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
        return NS(text='{"ok":true}', parsed=None,
                  usage_metadata=NS(prompt_token_count=100, candidates_token_count=20, thoughts_token_count=10) if self.usage else None,
                  candidates=[NS(content=types.Content(role="model", parts=[types.Part.from_text(text="ok")]))])

    def embed_content(self, **kwargs):
        with self.lock:
            self.embeddings.append(kwargs)
        return NS(embeddings=[NS(values=[.1] * 768, statistics=NS(token_count=5.0))])


def llm(models, account=None):
    result = GeminiLLM.__new__(GeminiLLM)
    result.model, result.fallback_model, result._thinking = "gemini-3.8-flash", "gemini-3.5-flash", "low"
    result.calls, result._record_dir, result._client = 0, None, NS(models=models)
    return result.with_budget(account) if account else result


def embedder(models, account=None):
    result = GeminiEmbedder.__new__(GeminiEmbedder)
    result.model, result.dim, result.calls, result._client = "gemini-embedding-2", 768, 0, NS(models=models)
    return result.with_budget(account) if account else result


def gen(model, purpose="review"):
    return model.generate(system="system", parts=["input"], schema=Out, purpose=purpose)


def test_gross_prices_round_up_and_unknown_price_fails_closed():
    assert cost_micros("gemini-3.8-flash", 1, 1) == 9
    assert cost_micros("gemini-3.5-flash", 1, 1) == 11
    assert cost_micros("gemini-embedding-2", 1) == 1
    ledger = MemoryBudgetLedger()
    with pytest.raises(BudgetError, match="verified budget price"):
        AccountBudget("a", ledger).reserve("unknown", 1)
    assert ledger.used("a", budget.today()) == 0


def test_atomic_reservations_exact_limit_and_no_concurrent_overspend():
    ledger = MemoryBudgetLedger()
    def take(i):
        return ledger.reserve(BudgetReservation("a", "2026-10-18", str(i), 100_000), 1_000_000)
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(take, range(100)))
    assert results.count(True) == 10 and ledger.used("a", "2026-10-18") == 1_000_000
    assert not ledger.reserve(BudgetReservation("a", "2026-10-18", "over", 1), 1_000_000)
    assert ledger.reserve(BudgetReservation("b", "2026-10-18", "b", 1_000_000), 1_000_000)


def test_rollover_and_idempotent_settlement_belongs_to_original_day(monkeypatch):
    ledger = MemoryBudgetLedger()
    account = AccountBudget("a", ledger)
    monkeypatch.setattr(budget, "today", lambda: "2026-10-18")
    hold = account.reserve("gemini-3.8-flash", 100, 100)
    assert ledger.reserve(hold, 1_000_000)  # repeated reservation cannot double-charge
    monkeypatch.setattr(budget, "today", lambda: "2026-10-19")
    account.settle(hold, "gemini-3.8-flash", 10, 10)
    account.settle(hold, "gemini-3.8-flash", 10, 10)
    assert ledger.used("a", "2026-10-18") == 90 and ledger.used("a", "2026-10-19") == 0
    account.reserve("gemini-3.8-flash", 100, 100)
    assert ledger.used("a", "2026-10-19") == 900


@pytest.mark.parametrize("purpose,cap", [("agent",4096),("review",16384),("transcribe",32768),("boxes",2048),("negotiation",4096)])
def test_each_purpose_reserves_before_one_sdk_attempt_and_settles_known_usage(purpose, cap):
    ledger, models = MemoryBudgetLedger(), Models()
    account = AccountBudget("real-user", ledger)
    original = models.generate_content
    def checked(**kwargs):
        assert ledger.used("real-user", budget.today()) == cost_micros(kwargs["model"], 200, cap)
        cfg = kwargs["config"]
        assert cfg.max_output_tokens == cap and cfg.candidate_count == 1
        assert cfg.automatic_continuation is False and cfg.automatic_function_calling.disable is True
        assert cfg.http_options.retry_options.attempts == 1
        return original(**kwargs)
    models.generate_content = checked
    out, _ = gen(llm(models, account), purpose)
    assert out.ok and len(models.generations) == 1
    assert ledger.used("real-user", budget.today()) == cost_micros("gemini-3.8-flash", 100, 30)


def test_count_tokens_has_full_system_tools_pdf_and_generation_config():
    models, ledger = Models(), MemoryBudgetLedger()
    model = llm(models, AccountBudget("a", ledger))
    model.chat(system="FULL SYSTEM", messages=[ChatMessage(role="user", text="all history")],
               tools=[ToolSpec("read_page", "description", {"type":"object"})], purpose="agent")
    count, actual = models.counts[-1], models.generations[-1]
    assert count["contents"] == actual["contents"]
    assert count["config"].system_instruction == "FULL SYSTEM"
    assert count["config"].tools == actual["config"].tools
    assert count["config"].generation_config.max_output_tokens == 4096
    model.generate(system="OCR", parts=[PdfPart(b"pdf bytes")], schema=Out, purpose="transcribe")
    assert models.counts[-1]["contents"][0].inline_data.data == b"pdf bytes"


def test_fallback_reserves_primary_failure_and_independent_fallback_success(monkeypatch):
    monkeypatch.setattr("app.adapters.gcp.time.sleep", lambda _: None)
    ledger, models = MemoryBudgetLedger(), Models([Failure(504), "ok"])
    model = llm(models, AccountBudget("a", ledger))
    _, usage = gen(model)
    assert [row["model"] for row in models.generations] == ["gemini-3.8-flash", "gemini-3.5-flash"]
    assert all(row["config"].http_options.retry_options.attempts == 1 for row in models.generations)
    assert ledger.used("a", budget.today()) == cost_micros("gemini-3.8-flash", 200, 16384) + cost_micros("gemini-3.5-flash",100,30)
    assert usage.fallback_model == "gemini-3.5-flash"


def test_retry_failure_retains_every_hold_and_missing_usage_never_refunds(monkeypatch):
    monkeypatch.setattr("app.adapters.gcp.time.sleep", lambda _: None)
    ledger, models = MemoryBudgetLedger(), Models([Failure(503), Failure(503)])
    with pytest.raises(UpstreamError):
        gen(llm(models, AccountBudget("a", ledger)))
    assert len(models.generations) == 2
    assert ledger.used("a", budget.today()) == 2 * cost_micros("gemini-3.8-flash", 200, 16384)
    unknown = Models(usage=False)
    gen(llm(unknown, AccountBudget("b", ledger)))
    assert ledger.used("b", budget.today()) == cost_micros("gemini-3.8-flash", 200, 16384)


def test_over_limit_or_unknown_input_count_never_calls_generation():
    for unknown_count in (False, True):
        models, ledger = Models(), MemoryBudgetLedger()
        if unknown_count:
            models.count_tokens = lambda **kw: NS(total_tokens=None)
        else:
            ledger.reserve(BudgetReservation("a", budget.today(), "full", 1_000_000), 1_000_000)
        with pytest.raises(BudgetError) as caught:
            gen(llm(models, AccountBudget("a", ledger)))
        assert caught.value.code == ("budget_unavailable" if unknown_count else "budget_exceeded")
        assert models.generations == []


def test_embeddings_reserve_per_worker_per_attempt_and_refund_observed_usage(monkeypatch):
    monkeypatch.setattr("app.adapters.gcp.time.sleep", lambda _: None)
    ledger, models = MemoryBudgetLedger(), Models()
    account = AccountBudget("a", ledger)
    original = models.embed_content
    failed = False
    def retry(**kwargs):
        nonlocal failed
        assert kwargs["config"].http_options.retry_options.attempts == 1
        if not failed:
            failed = True
            raise Failure(503)
        return original(**kwargs)
    models.embed_content = retry
    vectors, tokens = embedder(models, account).embed(["資料", "hello"], query=True)
    assert len(vectors) == 2 and tokens == 10
    assert ledger.used("a", budget.today()) >= cost_micros("gemini-embedding-2", len("資料".encode()) + 1024) + 2
    assert all(row["config"].task_type == "RETRIEVAL_QUERY" for row in models.embeddings)


def test_request_clones_carry_same_uid_into_kb_and_workers_without_global_state(services):
    models = Models()
    services.llm, services.embedder = llm(models), embedder(models)
    a, a_account = request_services(services, "alice")
    b, b_account = request_services(services, "bob")
    assert a.kb.embedder is a.embedder and b.kb.embedder is b.embedder
    assert services.kb.embedder is not a.embedder
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda svc: svc.embedder.embed(["x", "y"]), (a,b)))
    assert services.budget_ledger.used("alice", budget.today()) == 2
    assert services.budget_ledger.used("bob", budget.today()) == 2
    assert not hasattr(services.embedder, "_account_budget")
    services.budget_ledger.reserve(BudgetReservation("alice",budget.today(),"exhaust",999_998),1_000_000)
    with pytest.raises(BudgetError):
        a_account.admit()
    b_account.admit()


def test_sticky_budget_denial_cannot_be_hidden_by_inner_exception_handlers():
    account = AccountBudget("a", MemoryBudgetLedger(), limit=1)
    try:
        account.reserve("gemini-3.8-flash", 100)
    except Exception:
        pass  # like existing tool/index best-effort handlers
    with pytest.raises(BudgetError) as caught:
        account.check()
    assert caught.value.code == "budget_exceeded"


def test_firestore_reservation_and_settlement_are_atomic_idempotent_and_day_bound(monkeypatch):
    # Test the actual transaction body with fake refs; SDK transaction wrapper is replaced, never networked.
    data, lock, reads = {}, threading.RLock(), []
    class Ref:
        def __init__(self, key): self.key = key
        def collection(self, name): return Collection(self.key + "/" + name)
        def get(self, transaction=None):
            reads.append((self.key, transaction))
            return NS(exists=self.key in data, to_dict=lambda: data.get(self.key))
    class Collection:
        def __init__(self, key): self.key = key
        def document(self, key): return Ref(self.key + "/" + key)
    class Tx:
        def set(self, ref, value): data[ref.key] = dict(value)
    from google.cloud import firestore
    def transactional(fn):
        def run(tx, *args):
            with lock: return fn(tx, *args)
        return run
    monkeypatch.setattr(firestore, "transactional", transactional)
    ledger = FirestoreBudgetLedger.__new__(FirestoreBudgetLedger)
    ledger._col, ledger._db = Collection("accounts"), NS(transaction=Tx)
    holds = [BudgetReservation("a", "2026-10-18", str(i), 100_000) for i in range(20)]
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda r: ledger.reserve(r, 1_000_000), holds))
    assert results.count(True) == 10 and ledger.used("a","2026-10-18") == 1_000_000
    accepted = next(r for r, ok in zip(holds, results) if ok)
    assert ledger.reserve(accepted, 1_000_000)
    ledger.settle(accepted, 1)
    ledger.settle(accepted, 1)
    assert ledger.used("a","2026-10-18") == 900_001 and ledger.used("a","2026-10-19") == 0
    assert all(tx is not None for key, tx in reads if "/reservations/" in key)
