"""Key-date arithmetic and provenance: the model reads the pieces, code does the counting.

The model extracts, per key date, a date the contract states (base_date), optionally the length of the
term that starts then (term_months: the anchor is that term's last day), and an offset with its unit and
direction ("60 days before"). The calendar arithmetic is done here, so "at least 60 days before 31 August"
is 2 July, never an off-by-one from counting from the day after the term.

Each date is then classed, deterministically:
- stated: the contract writes this exact date (any common format, English or Malay month names);
- calculated: computed from a stated date, where the offset is in the quoted clause that sets it (and any
  term length is in the contract), and that quote is on the stated page;
- inferred: anything else. Its label says it is an estimate, so it is never shown as if the contract
  stated it.
Pure functions; no SDK imports.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_MONTHS = {
    "january": 1, "jan": 1, "januari": 1, "february": 2, "feb": 2, "februari": 2, "march": 3, "mar": 3, "mac": 3,
    "april": 4, "apr": 4, "may": 5, "mei": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "julai": 7,
    "august": 8, "aug": 8, "ogos": 8, "ogo": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11, "december": 12, "dec": 12, "disember": 12, "dis": 12,
}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th|hb|haribulan)?"
_DMY_WORDS = re.compile(rf"\b(\d{{1,2}}){_ORD}\s*(?:day\s+of\s+|hari\s+bulan\s+|haribulan\s+)?({_MON})\.?,?\s+(\d{{4}})\b",
                        re.I)
_MDY_WORDS = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}}){_ORD},?\s+(\d{{4}})\b", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_NUM = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")  # day first, as in Malaysia and Singapore


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def stated_dates(text: str) -> set[date]:
    """Every full calendar date written in `text`."""
    out: set[date] = set()
    for m in _DMY_WORDS.finditer(text):
        out.add(_make(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))))
    for m in _MDY_WORDS.finditer(text):
        out.add(_make(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))))
    for m in _ISO.finditer(text):
        out.add(_make(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _DMY_NUM.finditer(text):
        out.add(_make(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    out.discard(None)  # type: ignore[arg-type]
    return out


def add_months(d: date, months: int) -> date:
    """d plus whole months; the day is clamped to the end of a shorter month (31 Jan + 1 month = 28/29 Feb)."""
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    month += 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def parse_iso(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def anchor_date(base: date, term_months: int) -> date:
    """The anchor: the base date itself, or the last day of a term of `term_months` that starts on it
    (a 12-month term from 1 Sep 2026 ends on 31 Aug 2027)."""
    return base if term_months <= 0 else add_months(base, term_months) - timedelta(days=1)


def shift(anchor: date, offset: int, unit: str, direction: str) -> date:
    """`offset` `unit`s before or after `anchor`. Months and years keep the day of the month (clamped)."""
    sign = -1 if direction == "before" else 1
    n = abs(int(offset))
    if unit == "days":
        return anchor + timedelta(days=sign * n)
    if unit == "weeks":
        return anchor + timedelta(days=sign * 7 * n)
    if unit == "years":
        return add_months(anchor, sign * 12 * n)
    return add_months(anchor, sign * n)


def resolve(base_date: str, term_months: int, offset: int, unit: str, direction: str) -> date | None:
    """The date the pieces describe, or None without a usable base date."""
    base = parse_iso(base_date)
    if base is None:
        return None
    if offset < 0:  # a negative offset is a direction the model folded into the number
        offset, direction = -offset, ("after" if direction == "before" else "before")
    try:
        return shift(anchor_date(base, int(term_months)), offset, unit, direction)
    except (ValueError, OverflowError):
        return None


def classify(*, result: date | None, base_date: str, term_months: int, offset: int, contract_dates: set[date],
             contract_numbers: set[str], quote_numbers: set[str] | None) -> str | None:
    """stated | calculated | inferred (None without a date). `quote_numbers` is None when the source
    quote is missing or is not on the stated page."""
    if result is None:
        return None
    if result in contract_dates and term_months == 0 and offset == 0:
        return "stated"
    base = parse_iso(base_date)
    if base is None or base not in contract_dates:
        return "stated" if result in contract_dates else "inferred"
    term_ok = term_months <= 0 or str(term_months) in contract_numbers or (
        term_months % 12 == 0 and str(term_months // 12) in contract_numbers)
    offset_ok = offset == 0 or (quote_numbers is not None and str(abs(offset)) in quote_numbers)
    if term_months == 0 and offset == 0:
        return "stated"
    if term_ok and offset_ok:
        return "calculated"
    return "stated" if result in contract_dates else "inferred"


ESTIMATE_LABEL = " (estimate)"
ESTIMATE_RULE = "Not stated in the contract; estimated. "
