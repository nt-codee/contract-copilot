"""Real concurrent admission shares one account lock; exhausted SDK contention is bounded."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time

import pytest
from google.api_core.exceptions import Aborted
from google.cloud import firestore

from app.adapters.gcp_budget import FirestoreBudgetLedger
from app.ports import BudgetReservation


def ledger():
    result = FirestoreBudgetLedger.__new__(FirestoreBudgetLedger)
    result._db = SimpleNamespace(transaction=lambda: object())
    return result


def test_account_transactions_serialize_across_ledger_instances(monkeypatch):
    active = maximum = 0
    guard = threading.Lock()
    def run(tx, reservation, limit):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(.002)
        with guard:
            active -= 1
        return True
    monkeypatch.setattr(firestore, 'transactional', lambda callback: run)
    instances = [ledger() for _ in range(20)]
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(lambda i: instances[i].reserve(BudgetReservation('same-account', '2026-10-18', str(i), 1), 100), range(20)))
    assert all(results) and maximum == 1


def test_wrapped_aborted_retries_same_reservation_then_succeeds(monkeypatch):
    seen = []
    def run(tx, reservation, limit):
        seen.append(reservation)
        if len(seen) < 3:
            raise ValueError('Failed to commit transaction') from Aborted('contention')
        return True
    monkeypatch.setattr(firestore, 'transactional', lambda callback: run)
    monkeypatch.setattr('app.adapters.gcp_budget.time.sleep', lambda _: None)
    hold = BudgetReservation('a', '2026-10-18', 'same-id', 1)
    assert ledger().reserve(hold, 100)
    assert seen == [hold, hold, hold]


def test_retries_are_bounded_and_other_failures_are_not_retried(monkeypatch):
    calls = []
    def run(tx, reservation, limit):
        calls.append(reservation)
        raise Aborted('contention')
    monkeypatch.setattr(firestore, 'transactional', lambda callback: run)
    monkeypatch.setattr('app.adapters.gcp_budget.time.sleep', lambda _: None)
    hold = BudgetReservation('a', '2026-10-18', 'same-id', 1)
    with pytest.raises(Aborted):
        ledger().reserve(hold, 100)
    assert len(calls) == 3
    def invalid(*args):
        raise ValueError('reservation mismatch')
    monkeypatch.setattr(firestore, 'transactional', lambda callback: invalid)
    with pytest.raises(ValueError, match='reservation mismatch'):
        ledger().reserve(hold, 100)
