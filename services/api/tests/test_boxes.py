import pymupdf
import pytest

from app.pipeline import box_from_2d, locate_boxes
from pdfgen import make_pdf


@pytest.fixture(scope="module")
def doc():
    d = pymupdf.open(stream=make_pdf(), filetype="pdf")
    yield d
    d.close()


def _in_range(boxes):
    return all(len(b) == 4 and all(0 <= v <= 1 for v in b) and b[2] > b[0] and b[3] > b[1] for b in boxes)


def test_exact_single_line_hit(doc):
    boxes = locate_boxes(doc[1], "The Customer shall be liable without limit for any loss suffered by the Vendor.")
    assert len(boxes) == 1 and _in_range(boxes)
    x0, y0, x1, y1 = boxes[0]
    assert x0 < 0.2 and y0 < 0.2 and all(round(v, 4) == v for v in boxes[0])


def test_two_line_quote_gives_two_rects_in_reading_order(doc):
    boxes = locate_boxes(doc[1], "The Vendor's total liability is limited to the fees paid in the one month before the claim")
    assert len(boxes) == 2 and _in_range(boxes)
    assert boxes[0][1] < boxes[1][1]  # top line first


def test_head_and_tail_fallback_when_middle_differs(doc):
    # middle word altered so the whole string does not match; head and tail still do
    quote = ("The Customer shall be liable without limit for any loss suffered by the Supplier. "
             "5.2 The Vendor's total liability is limited to the fees paid in the one month before the claim.")
    boxes = locate_boxes(doc[1], quote)
    assert len(boxes) == 3 and _in_range(boxes)


def test_not_found_gives_empty(doc):
    assert locate_boxes(doc[1], "This sentence is nowhere in the contract at all.") == []
    assert locate_boxes(doc[1], "") == []


def test_box_2d_conversion():
    assert box_from_2d([100, 50, 130, 900]) == [0.05, 0.1, 0.9, 0.13]
    assert box_from_2d([500, 500, 400, 600]) is None  # ymin > ymax
    assert box_from_2d([1, 2, 3]) is None
    assert box_from_2d([-5, 0, 1200, 1000]) == [0.0, 0.0, 1.0, 1.0]  # clamped
