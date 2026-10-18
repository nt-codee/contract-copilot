"""The API must match shared/contracts/review.schema.json exactly."""

import json
from pathlib import Path
from typing import get_args

import pytest
from jsonschema import Draft202012Validator

from app import models
from app.config import REPO_ROOT

SCHEMA = json.loads((REPO_ROOT / "shared" / "contracts" / "review.schema.json").read_text())
RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def validator(defname: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/$defs/{defname}", "$defs": SCHEMA["$defs"]},
                                format_checker=Draft202012Validator.FORMAT_CHECKER)


def assert_valid(defname: str, instance) -> None:
    errors = sorted(validator(defname).iter_errors(instance), key=lambda e: e.path)
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


@pytest.mark.parametrize("alias,defname", [
    (models.ContractType, "ContractType"), (models.Risk, "Risk"), (models.ClauseType, "ClauseType"),
])
def test_enums_match_schema(alias, defname):
    assert list(get_args(alias)) == SCHEMA["$defs"][defname]["enum"]


def test_review_option_enums_and_defaults_match_schema():
    props = SCHEMA["$defs"]["ReviewOptions"]["properties"]
    for field in ("contract_type", "jurisdiction", "perspective"):
        assert list(get_args(models.ReviewOptions.model_fields[field].annotation)) == props[field]["enum"], field
        assert models.ReviewOptions.model_fields[field].default == props[field]["default"]
    assert models.ReviewOptions().apply_company_rules is True and props["apply_company_rules"]["default"] is True
    assert models.ReviewOptions().company_doc_ids is None and props["company_doc_ids"]["default"] is None
    assert SCHEMA["$defs"]["Provenance"]["properties"]["options"]["anyOf"][0] == {"$ref": "#/$defs/ReviewOptions"}


def test_nested_enums_match_schema():
    defs = SCHEMA["$defs"]
    assert list(get_args(models.AuditAction)) == defs["AuditEvent"]["properties"]["action"]["enum"]
    assert list(get_args(models.CitationKind)) == defs["Citation"]["properties"]["kind"]["enum"]
    assert list(get_args(models.TraceAction)) == defs["TraceStep"]["properties"]["action"]["enum"]


@pytest.mark.parametrize("model,defname", [
    (models.Finding, "Finding"), (models.MissingClause, "MissingClause"), (models.Verdict, "Verdict"),
    (models.Metrics, "Metrics"), (models.ReviewResult, "ReviewResult"),
    (models.ReviewSummary, "ReviewSummary"), (models.NegotiationDraft, "NegotiationDraft"),
    (models.SearchHit, "SearchHit"), (models.Sample, "Sample"),
    (models.Citation, "Citation"), (models.KeyDate, "KeyDate"), (models.KnowledgeEntry, "KnowledgeEntry"), (models.TraceStep, "TraceStep"), (models.CompanyDoc, "CompanyDoc"),
    (models.User, "User"), (models.AuditEvent, "AuditEvent"), (models.Provenance, "Provenance"), (models.ReviewOptions, "ReviewOptions"),
])
def test_model_fields_match_schema(model, defname):
    spec = SCHEMA["$defs"][defname]
    assert set(model.model_fields) == set(spec["properties"]), defname
    required = {n for n, f in model.model_fields.items() if f.is_required()}
    assert required <= set(spec["required"]), defname


def test_fixture_review_result_validates(client, pdf_bytes):
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    assert_valid("ReviewResult", body)
    assert_valid("ReviewSummary", client.get("/api/reviews").json()[0])
    assert_valid("NegotiationDraft", client.post(f"/api/reviews/{body['id']}/negotiation").json())
    for hit in client.get("/api/search", params={"q": "liability"}).json():
        assert_valid("SearchHit", hit)
    for s in client.get("/api/samples").json():
        assert_valid("Sample", s)


@pytest.mark.parametrize("name", ["review_result.json", "review_result_scanned.json"])
def test_recorded_real_gemini_result_validates(name):
    """Real gemini-3.8-flash output (2026-10-18, synthetic test PDF) must match the contract."""
    assert_valid("ReviewResult", json.loads((RECORDED / name).read_text()))


def test_legacy_review_without_boxes_loads_with_empty_boxes():
    """Reviews cached before `boxes` existed (e.g. in Firestore) must still load."""
    raw = json.loads((RECORDED / "review_result_scanned.json").read_text())
    assert all("boxes" not in f for f in raw["findings"])
    r = models.ReviewResult.model_validate(raw)
    assert r.findings and all(f.boxes == [] for f in r.findings)
    assert all(f["boxes"] == [] for f in r.model_dump(mode="json")["findings"])


def test_recorded_boxes_in_range():
    raw = json.loads((RECORDED / "review_result.json").read_text())
    assert all(f["boxes"] for f in raw["findings"])
    assert all(0 <= v <= 1 for f in raw["findings"] for b in f["boxes"] for v in b)
