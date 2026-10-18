"""Google Cloud adapters for the knowledge store and playbook overrides.

Firestore collection `knowledge` (768-d COSINE vector index on (kind, embedding), created by infra),
markdown files in the knowledge bucket under kb/<kb_version>/<id>.md, and Firestore collection
`playbook_overrides`. SDKs are imported lazily inside constructors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator

from app.adapters.gcp import _firestore_client
from app.config import Settings
from app.knowledge_store import KnowledgeDoc
from app.playbook_overrides import PlaybookOverride, PlaybookOverrideIn

log = logging.getLogger("cc.gcp.knowledge")

_META = ["id", "kind", "jurisdiction", "contract_type", "clause_types", "title", "citation", "plain", "excerpt",
         "url", "source_url", "gcs_uri", "kb_version", "content_hash"]


def _doc_key(doc_id: str) -> str:
    """Firestore document ids cannot contain '/': playbook:nda/liability -> playbook:nda__liability."""
    return doc_id.replace("/", "__")


class FirestoreKnowledgeStore:
    COLLECTION = "knowledge"
    FIELD = "embedding"

    def __init__(self, settings: Settings):
        from google.cloud import storage

        self._db = _firestore_client(settings)
        self._col = self._db.collection(self.COLLECTION)
        self._bucket_name = settings.knowledge_bucket
        self._bucket = storage.Client(project=settings.gcp_project).bucket(self._bucket_name)

    @staticmethod
    def _from(d: dict) -> KnowledgeDoc:
        return KnowledgeDoc(
            id=d["id"], kind=d["kind"], title=d.get("title") or "", jurisdiction=d.get("jurisdiction"),
            contract_type=d.get("contract_type"), clause_types=list(d.get("clause_types") or []),
            citation=d.get("citation"), plain=d.get("plain") or "", excerpt=d.get("excerpt") or "",
            url=d.get("url"), source_url=d.get("source_url"), gcs_uri=d.get("gcs_uri") or "",
            kb_version=d.get("kb_version") or "", content_hash=d.get("content_hash") or "")

    def content_hashes(self) -> dict[str, str]:
        return {s.get("id"): s.get("content_hash") or "" for s in self._col.select(["id", "content_hash"]).stream()}

    def upsert(self, docs: list[KnowledgeDoc]) -> None:
        from google.cloud.firestore_v1.vector import Vector

        for d in docs:
            key = f"kb/{d.kb_version}/{d.id}.md"
            self._bucket.blob(key).upload_from_string(d.body.encode("utf-8"), content_type="text/markdown; charset=utf-8")
            d.gcs_uri = f"gs://{self._bucket_name}/{key}"
        for start in range(0, len(docs), 400):
            batch = self._db.batch()
            for d in docs[start:start + 400]:
                rec = {k: getattr(d, k) for k in _META}
                rec[self.FIELD] = Vector(d.embedding)
                batch.set(self._col.document(_doc_key(d.id)), rec)
            batch.commit()

    def list_docs(self) -> list[KnowledgeDoc]:
        return [self._from(s.to_dict()) for s in self._col.select(_META).stream()]

    def get_doc(self, doc_id: str) -> KnowledgeDoc | None:
        snap = self._col.document(_doc_key(doc_id)).get()
        return self._from(snap.to_dict()) if snap.exists else None

    def _blob(self, doc_id: str):
        doc = self.get_doc(doc_id)
        if doc is None or not doc.gcs_uri.startswith(f"gs://{self._bucket_name}/"):
            return None  # only ever read the object our own record points at
        return self._bucket.blob(doc.gcs_uri[len(f"gs://{self._bucket_name}/"):])

    def read_markdown(self, doc_id: str) -> bytes | None:
        from google.api_core.exceptions import NotFound

        blob = self._blob(doc_id)
        try:
            return blob.download_as_bytes() if blob else None
        except NotFound:
            return None

    def stream_markdown(self, doc_id: str) -> Iterator[bytes] | None:
        blob = self._blob(doc_id)
        if blob is None:
            return None

        def chunks() -> Iterator[bytes]:
            with blob.open("rb") as fh:
                while block := fh.read(64 * 1024):
                    yield block

        return chunks()

    def vector_search(self, vector: list[float], kinds: list[str], limit: int) -> list[tuple[KnowledgeDoc, float]]:
        from google.cloud.firestore_v1.base_query import FieldFilter
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        out: list[tuple[KnowledgeDoc, float]] = []
        for kind in kinds:  # one equality filter per kind, matching the (kind, embedding) index
            q = self._col.where(filter=FieldFilter("kind", "==", kind)).find_nearest(
                vector_field=self.FIELD, query_vector=Vector(vector), distance_measure=DistanceMeasure.COSINE,
                limit=limit, distance_result_field="vector_distance")
            for s in q.stream():
                d = s.to_dict()
                out.append((self._from(d), 1.0 - float(d.get("vector_distance", 1.0))))
        out.sort(key=lambda t: -t[1])
        return out[:limit]


class FirestorePlaybookOverrides:
    COLLECTION = "playbook_overrides"

    def __init__(self, settings: Settings):
        self._col = _firestore_client(settings).collection(self.COLLECTION)

    @staticmethod
    def _key(owner: str, contract_type: str, clause_type: str) -> str:
        return f"{owner}__{contract_type}__{clause_type}"

    @staticmethod
    def _from(d: dict) -> PlaybookOverride:
        return PlaybookOverride(standard=d.get("standard", ""), red_flags=list(d.get("red_flags") or []),
                                ask=d.get("ask", ""), note=d.get("note", ""), updated_by=d.get("updated_by", ""),
                                updated_at=d["updated_at"])

    def get(self, owner, contract_type, clause_type):
        snap = self._col.document(self._key(owner, contract_type, clause_type)).get()
        return self._from(snap.to_dict()) if snap.exists else None

    def list(self, owner):
        from google.cloud.firestore_v1.base_query import FieldFilter

        out = {}
        for s in self._col.where(filter=FieldFilter("owner", "==", owner)).stream():
            d = s.to_dict()
            out[(d["contract_type"], d["clause_type"])] = self._from(d)
        return out

    def put(self, owner, contract_type, clause_type, body: PlaybookOverrideIn, updated_by):
        now = datetime.now(timezone.utc)
        rec = {**body.cleaned().model_dump(), "updated_by": updated_by, "updated_at": now, "owner": owner,
               "contract_type": contract_type, "clause_type": clause_type}
        self._col.document(self._key(owner, contract_type, clause_type)).set(rec)
        return self._from(rec)

    def delete(self, owner, contract_type, clause_type):
        ref = self._col.document(self._key(owner, contract_type, clause_type))
        existed = ref.get().exists
        ref.delete()
        return existed
