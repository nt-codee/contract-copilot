"""Account/day paid-call admission. Reservations precede upstream attempts; unknown usage stays held.

Enforcement uses gross configured token prices, not a promise to reproduce the provider invoice.
HTTP adapters carry an explicit immutable account binding into every nested worker; CLI adapters
without this binding remain an explicit offline/administrative path.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import threading
import logging
import uuid

from app.config import ACCOUNT_DAILY_LIMIT_MICROS, ENFORCEMENT_PRICES
from app.ports import BudgetReservation

log = logging.getLogger(__name__)

# Abuse guard: a clearly failed attempt may be settled down to its input cost at most this many times per
# account per UTC day; after that a failure keeps the full worst-case hold.
MAX_RELEASES_PER_DAY = 20

BUDGET_MESSAGE = "You have reached the $1.00 account review budget for this UTC day. It resets at 00:00 UTC."


class BudgetError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def cost_micros(model: str, inputs: int, outputs: int = 0) -> int:
    prices = ENFORCEMENT_PRICES.get(model)
    if prices is None:
        raise BudgetError(503, "budget_unavailable", "This AI model has no verified budget price. Please try again later.")
    if type(inputs) is not int or type(outputs) is not int or min(inputs, outputs) < 0:
        raise BudgetError(503, "budget_unavailable", "AI token accounting is unavailable. Please try again later.")
    return int((Decimal(prices[0]) * inputs + Decimal(prices[1]) * outputs).to_integral_value(rounding=ROUND_CEILING))


class AccountBudget:
    def __init__(self, uid, ledger, limit=ACCOUNT_DAILY_LIMIT_MICROS):
        self.uid, self.ledger, self.limit = uid, ledger, limit
        self._error = None
        self._lock = threading.Lock()

    def fail(self, error):
        with self._lock:
            if self._error is None:
                self._error = error
        raise error

    def check(self):
        with self._lock:
            error = self._error
        if error is not None:
            raise error

    def require(self):
        self.check()
        if not self.uid:
            self.fail(BudgetError(401, "sign_in_required", "Sign in with Google to start a new AI operation."))
        if self.ledger is None:
            self.fail(BudgetError(503, "budget_unavailable", "Account budget accounting is unavailable. Please try again later."))

    def admit(self):
        self.require()
        try:
            used = self.ledger.used(self.uid, today())
        except Exception:
            self.fail(BudgetError(503, "budget_unavailable", "Account budget accounting is unavailable. Please try again later."))
        if used >= self.limit:
            self.fail(BudgetError(429, "budget_exceeded", BUDGET_MESSAGE))

    def reserve(self, model, inputs, outputs=0):
        self.require()
        try:
            amount = cost_micros(model, inputs, outputs)
            reservation = BudgetReservation(self.uid, today(), uuid.uuid4().hex, amount)
            accepted = self.ledger.reserve(reservation, self.limit)
        except BudgetError as error:
            self.fail(error)
        except Exception:
            self.fail(BudgetError(503, "budget_unavailable", "Account budget accounting is unavailable. Please try again later."))
        if not accepted:
            self.fail(BudgetError(429, "budget_exceeded", BUDGET_MESSAGE))
        return reservation

    def settle(self, reservation, model, inputs, outputs=0):
        # Only call this on observed successful usage. A timeout/unknown usage retains the original hold.
        try:
            actual = cost_micros(model, inputs, outputs)
            self.ledger.settle(reservation, actual)
        except BudgetError as error:
            self.fail(error)
        except Exception:
            self.fail(BudgetError(503, "budget_unavailable", "Account budget accounting is unavailable. Please try again later."))
        if actual > reservation.micros:
            self.fail(BudgetError(503, "budget_unavailable", "AI usage exceeded its reservation. Further AI work is blocked."))

    def release_failed(self, reservation, model, inputs):
        """Settle a definite no-output failure down to the input-token cost (output 0). Returns True if released.
        Any doubt (cap reached, ledger error, no release support) keeps the full hold; never raises."""
        try:
            release = getattr(self.ledger, "release", None)
            if release is None:
                return False
            actual = cost_micros(model, inputs, 0)
            if actual > reservation.micros:
                return False
            return bool(release(reservation, actual, MAX_RELEASES_PER_DAY))
        except Exception:
            log.warning("budget hold release failed; full hold kept", exc_info=True)
            return False


class GuardedAdapter:
    """Local fake adapters cost nothing, but must obey the same HTTP account/sign-in policy."""
    def __init__(self, adapter, account):
        self._adapter, self._account = adapter, account

    def __getattr__(self, name):
        return getattr(self._adapter, name)

    def chat(self, **kwargs):
        self._account.admit()
        return self._adapter.chat(**kwargs)

    def generate(self, **kwargs):
        self._account.admit()
        return self._adapter.generate(**kwargs)

    def embed(self, *args, **kwargs):
        self._account.admit()
        return self._adapter.embed(*args, **kwargs)


def request_services(services, uid):
    account = AccountBudget(uid, services.budget_ledger)

    def bind(adapter):
        if hasattr(adapter, "with_budget"):
            return adapter.with_budget(account)
        return GuardedAdapter(adapter, account)

    embedder, llm = bind(services.embedder), bind(services.llm)
    kb = copy.copy(services.kb)
    if hasattr(kb, "embedder"):
        kb.embedder = embedder
    return replace(services, llm=llm, embedder=embedder, kb=kb), account
