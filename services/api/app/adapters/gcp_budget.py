"""Atomic shared account/day ledger; one counter and separate idempotent reservation documents."""
import hashlib
import random
import threading
import time

# Shared across request-scoped ledgers, bounded in memory. Keep provider calls parallel;
# only serialize the short reservation/settlement transactions for an account.
_ACCOUNT_LOCKS = tuple(threading.RLock() for _ in range(64))


class FirestoreBudgetLedger:
    def __init__(self, settings):
        from app.adapters.gcp import _firestore_client
        self._db = _firestore_client(settings)
        self._col = self._db.collection("account_daily_spend")

    @staticmethod
    def key(uid, day):
        return hashlib.sha256(uid.encode("utf-8")).hexdigest() + "-" + day

    def _refs(self, reservation):
        account = self._col.document(self.key(reservation.uid, reservation.day))
        return account, account.collection("reservations").document(reservation.id)

    def _transaction(self, callback, reservation, *args):
        from google.cloud import firestore
        from google.api_core.exceptions import Aborted
        lock = _ACCOUNT_LOCKS[int(hashlib.sha256(reservation.uid.encode()).hexdigest(), 16) % len(_ACCOUNT_LOCKS)]
        with lock:
            for attempt in range(3):
                try:
                    return firestore.transactional(callback)(self._db.transaction(), reservation, *args)
                except (Aborted, ValueError) as exc:
                    # The SDK wraps exhausted ABORTED retries in ValueError. Retry only that
                    # transient failure, with the SAME idempotent reservation; never bypass a cap.
                    cause = exc
                    while cause is not None and not isinstance(cause, Aborted):
                        cause = cause.__cause__
                    if cause is None or attempt == 2:
                        raise
                    time.sleep(random.uniform(.05, .15) * (2 ** attempt))

    def reserve(self, reservation, limit):
        return self._transaction(self._reserve, reservation, limit)

    def _reserve(self, tx, reservation, limit):
        account, hold = self._refs(reservation)
        counter, previous = account.get(transaction=tx), hold.get(transaction=tx)
        if previous.exists:
            if previous.to_dict()["reserved"] != reservation.micros:
                raise ValueError("reservation mismatch")
            return True
        total = int((counter.to_dict() or {}).get("micros", 0)) if counter.exists else 0
        if reservation.micros < 0 or total + reservation.micros > limit:
            return False
        tx.set(account, {"micros": total + reservation.micros, "day": reservation.day})
        tx.set(hold, {"reserved": reservation.micros, "actual": None})
        return True

    def settle(self, reservation, actual_micros):
        self._transaction(self._settle, reservation, actual_micros)

    def _settle(self, tx, reservation, actual_micros):
        account, hold = self._refs(reservation)
        counter, previous = account.get(transaction=tx), hold.get(transaction=tx)
        if not previous.exists or not counter.exists or actual_micros < 0:
            raise ValueError("invalid settlement")
        data = previous.to_dict()
        if data["reserved"] != reservation.micros:
            raise ValueError("reservation mismatch")
        if data["actual"] is not None:
            if data["actual"] != actual_micros:
                raise ValueError("settlement mismatch")
            return
        tx.set(account, {"micros": int(counter.to_dict()["micros"]) + actual_micros - reservation.micros,
                         "day": reservation.day})
        tx.set(hold, {"reserved": reservation.micros, "actual": actual_micros})

    def used(self, uid, day):
        snap = self._col.document(self.key(uid, day)).get()
        return int(snap.to_dict()["micros"]) if snap.exists else 0
