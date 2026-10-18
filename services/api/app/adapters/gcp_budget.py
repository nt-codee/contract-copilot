"""Atomic shared account/day ledger; one counter and separate idempotent reservation documents."""
import hashlib


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

    def reserve(self, reservation, limit):
        from google.cloud import firestore
        return firestore.transactional(self._reserve)(self._db.transaction(), reservation, limit)

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
        from google.cloud import firestore
        firestore.transactional(self._settle)(self._db.transaction(), reservation, actual_micros)

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
