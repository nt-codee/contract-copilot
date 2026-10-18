"""Firestore rejects arrays of arrays, so boxes are stored as maps and restored on read."""

import json
from pathlib import Path

from app.adapters.gcp import _from_firestore, _to_firestore
from app.models import ReviewResult

RECORDED = Path(__file__).parent / "fixtures" / "recorded" / "review_result.json"


def _has_nested_list(v) -> bool:
    if isinstance(v, list):
        return any(isinstance(x, list) or _has_nested_list(x) for x in v)
    if isinstance(v, dict):
        return any(_has_nested_list(x) for x in v.values())
    return False


def test_no_nested_lists_and_round_trip():
    review = ReviewResult.model_validate(json.loads(RECORDED.read_text()))
    assert any(f.boxes for f in review.findings)
    stored = _to_firestore(review)
    assert not _has_nested_list(stored)
    assert stored["findings"][0]["boxes"][0].keys() == {"x0", "y0", "x1", "y1"}
    assert _from_firestore(stored) == review


def test_legacy_doc_without_boxes_loads():
    review = ReviewResult.model_validate(json.loads(RECORDED.read_text()))
    stored = _to_firestore(review)
    for f in stored["findings"]:
        del f["boxes"]
    assert all(f.boxes == [] for f in _from_firestore(stored).findings)
