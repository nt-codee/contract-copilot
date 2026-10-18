"""Quote matching that survives real PDFs, without accepting words that are not on the page.

Real text layers differ from what a model copies: ligatures (U+FB01 "fi"), curly quotes, dashes,
non-breaking and zero-width spaces, soft hyphens, words hyphenated across line ends, running headers,
footers and page numbers that land between two lines of a clause, bullet glyphs, and columns.

Both sides are reduced to the same token stream:
  - every character goes through NFKD, combining marks are dropped and the rest is case-folded, so a
    ligature becomes "fi", "é" becomes "e" and full-width letters become ASCII;
  - a token is a run of letters and digits; everything else (punctuation, quotes, dashes, bullets,
    spaces of any kind, zero-width characters, soft hyphens) separates tokens and is never compared;
  - lines that are page furniture (a page number, "Page 2 of 4 ...", or a short line repeated on most
    pages) are removed from the page, and the same token runs are removed from the quote.

Matching, in order:
  1. exact: the quote's tokens, concatenated without separators, occur in the page's concatenated
     tokens starting and ending on token boundaries. Concatenating makes "termi-" + "nation" equal
     "termination" while still requiring every letter and digit, in order.
  2. fuzzy (quotes of at least FUZZY_MIN_TOKENS tokens): the best window of the page's tokens whose
     length is within LENGTH_SLACK of the quote's scores at least FUZZY_THRESHOLD (rapidfuzz ratio over
     the space-joined tokens, 0-100) AND has exactly the quote's numbers and negations. A paraphrase
     scores well below; a changed number or a dropped "not" is refused whatever the score.
  A quote that crosses a page break is matched exactly only (match_across).

A match returns token indices; each token keeps its offsets in the original text (for the displayed
span) or the rect of its PyMuPDF word (for boxes), so the same matcher serves both.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from math import ceil

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 92.0
FUZZY_MIN_TOKENS = 6
LENGTH_SLACK = 0.1  # fuzzy windows are len(quote) +/- max(1, 10%) tokens
MIN_QUOTE_CHARS = 12  # letters and digits; shorter quotes prove nothing
MAX_FURNITURE_TOKENS = 12

_PAGE_NO = re.compile(r"^(?:(?:page|halaman|muka surat|ms|hlm|p)\s*)?\d{1,4}(?:\s*(?:of|daripada|drp|dari)\s*\d{1,4})?$")
_PAGE_LEAD = re.compile(r"^(?:page|halaman|muka surat)\s*\d{1,4}(?:\s*(?:of|daripada|drp|dari)\s*\d{1,4})?\b")
_PAGE_TAIL = re.compile(r"\b(?:page|halaman|muka surat)\s*\d{1,4}(?:\s*(?:of|daripada|drp|dari)\s*\d{1,4})?$")


@dataclass(frozen=True)
class Tok:
    text: str  # normalised
    start: int  # offset in the original text (or the word index, for PDF words)
    end: int
    line: int  # line number in the source, for furniture removal and box merging


def fold(ch: str) -> str:
    """One original character -> its comparable form ('' for combining marks)."""
    return "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).casefold()


def tokenize(text: str) -> list[Tok]:
    """Tokens of `text` with their original offsets and line numbers."""
    toks: list[Tok] = []
    buf: list[str] = []
    start = -1
    line = 0
    tok_line = 0
    for i, ch in enumerate(text):
        f = fold(ch)
        if f and all(c.isalnum() for c in f):
            if start < 0:
                start, tok_line = i, line
            buf.append(f)
            continue
        if f == "" and start >= 0 and unicodedata.combining(ch):
            continue  # an accent inside a word
        if start >= 0:
            toks.append(Tok("".join(buf), start, i, tok_line))
            buf, start = [], -1
        if ch == "\n":
            line += 1
    if start >= 0:
        toks.append(Tok("".join(buf), start, len(text), tok_line))
    return toks


def _line_key(toks: list[Tok]) -> str:
    return " ".join(t.text for t in toks)


def _by_line(toks: list[Tok]) -> dict[int, list[Tok]]:
    out: dict[int, list[Tok]] = {}
    for t in toks:
        out.setdefault(t.line, []).append(t)
    return out


def is_page_number(key: str) -> bool:
    if len(key.split()) > MAX_FURNITURE_TOKENS:
        return False
    return bool(_PAGE_NO.match(key) or _PAGE_LEAD.match(key) or _PAGE_TAIL.search(key))


def furniture(pages_tokens: list[list[Tok]]) -> set[str]:
    """Line keys that are page furniture: short lines repeated on at least half the pages (two or more)."""
    if len(pages_tokens) < 2:
        return set()
    seen: dict[str, int] = {}
    for toks in pages_tokens:
        for key in {_line_key(ln) for ln in _by_line(toks).values()}:
            if key and len(key.split()) <= MAX_FURNITURE_TOKENS:
                seen[key] = seen.get(key, 0) + 1
    need = max(2, ceil(len(pages_tokens) / 2))
    return {k for k, n in seen.items() if n >= need}


def strip_furniture(toks: list[Tok], repeated: set[str]) -> tuple[list[Tok], list[tuple[str, ...]]]:
    """Drop furniture lines; also return their token runs so they can be cut from a quote."""
    keep: list[Tok] = []
    runs: list[tuple[str, ...]] = []
    for _, ln in sorted(_by_line(toks).items()):
        key = _line_key(ln)
        if key in repeated or is_page_number(key):
            if len(ln) >= 2:
                runs.append(tuple(t.text for t in ln))
            continue
        keep.extend(ln)
    return keep, runs


def cut_runs(q: list[str], runs: list[tuple[str, ...]]) -> list[str]:
    """Remove furniture token runs from the quote (a model often copies a footer into a quote that
    crosses a page break). Runs of one token are never cut: too easy to hit a real word."""
    for run in sorted(set(runs), key=len, reverse=True):
        n = len(run)
        i = 0
        while i + n <= len(q):
            if tuple(q[i:i + n]) == run:
                del q[i:i + n]
            else:
                i += 1
    return q


@dataclass(frozen=True)
class Match:
    first: int  # token index in the page stream, inclusive
    last: int  # inclusive
    how: str  # "normalised" | "fuzzy" ("exact" is decided by the caller on the raw strings)
    score: float


def find(q: list[str], p: list[str], *, fuzzy: bool = True) -> Match | None:
    """Locate quote tokens `q` in page tokens `p`. Exact (on concatenated tokens), else bounded fuzzy."""
    if not q or not p or sum(len(t) for t in q) < MIN_QUOTE_CHARS:
        return None
    joined = "".join(p)
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    pos = 0
    for i, t in enumerate(p):
        starts[pos] = i
        pos += len(t)
        ends[pos] = i
    needle = "".join(q)
    at = joined.find(needle)
    while at >= 0:
        if at in starts and at + len(needle) in ends:
            return Match(starts[at], ends[at + len(needle)], "normalised", 100.0)
        at = joined.find(needle, at + 1)
    if not fuzzy or len(q) < FUZZY_MIN_TOKENS:
        return None
    target = " ".join(q)
    must = _decisive(q)
    slack = max(1, int(len(q) * LENGTH_SLACK))
    best: Match | None = None
    for n in range(max(1, len(q) - slack), len(q) + slack + 1):
        for i in range(0, len(p) - n + 1):
            s = fuzz.ratio(target, " ".join(p[i:i + n]), score_cutoff=FUZZY_THRESHOLD)
            if s and (best is None or s > best.score) and _decisive(p[i:i + n]) == must:
                best = Match(i, i + n - 1, "fuzzy", round(float(s), 1))
    return best


# a fuzzy match may differ in spelling, never in a number or a negation: "30 days" is not "90 days"
NEGATIONS = frozenset("not no never nor without none neither unless except tidak bukan tanpa jangan kecuali".split())


def _decisive(toks: list[str]) -> list[str]:
    return sorted(t for t in toks if any(c.isdigit() for c in t) or t in NEGATIONS)


# ---- text pages (verification) ----------------------------------------------------------------


@dataclass
class TextHit:
    span: str  # the page's own words, furniture removed, whitespace collapsed
    how: str  # exact | normalised | fuzzy
    score: float
    start: int = 0  # offset in the (first) page's text where the matched words begin


_HYPHEN_BREAK = re.compile(r"(\w)[-\u00ad]\s*\n\s*(\w)")
_SPACES = re.compile(r"[\s\u00a0\u200b\u200c\u200d\u2060\ufeff]+")


def _display(text: str) -> str:
    text = _HYPHEN_BREAK.sub(r"\1\2", text).replace("\u00ad", "")
    return _SPACES.sub(" ", text).strip()


def _span(text: str, toks: list[Tok], first: int, last: int, quote: str) -> str:
    """Original text of tokens first..last, joining the pieces either side of removed furniture, with a
    closing punctuation mark the quote also ends with."""
    pieces, a = [], toks[first]
    prev = a
    for t in toks[first + 1:last + 1]:
        if _skipped(text, prev, t):  # furniture was removed here: keep it out of the displayed quote
            pieces.append(text[a.start:prev.end])
            a = t
        prev = t
    end = prev.end
    q = quote.strip()
    if q and not q[-1].isalnum() and end < len(text) and _mark(text[end]) == _mark(q[-1]):
        end += 1
    if q and not q[0].isalnum() and a.start > 0 and _mark(text[a.start - 1]) == _mark(q[0]):
        a = Tok(a.text, a.start - 1, a.end, a.line)
    pieces.append(text[a.start:end])
    return " ".join(_display(p) for p in pieces)


_MARKS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
                        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
                        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
                        "\u2212": "-"})


def _mark(ch: str) -> str:
    """Punctuation compared loosely: curly and straight quotes alike, every dash a hyphen."""
    return unicodedata.normalize("NFKC", ch).translate(_MARKS)


def _skipped(text: str, a: Tok, b: Tok) -> bool:
    """True if letters or digits (removed furniture) sit between tokens a and b."""
    return any(c.isalnum() for c in text[a.end:b.start])


def page_stream(text: str, repeated: set[str] | None = None) -> tuple[list[Tok], list[tuple[str, ...]]]:
    return strip_furniture(tokenize(text), repeated or set())


def match_text(quote: str, text: str, repeated: set[str] | None = None) -> TextHit | None:
    toks, runs = page_stream(text, repeated)
    q = cut_runs([t.text for t in tokenize(quote)], runs)
    m = find(q, [t.text for t in toks])
    if m is None:
        return None
    how = "exact" if quote.strip() and quote.strip() in text else m.how
    return TextHit(_span(text, toks, m.first, m.last, quote), how, m.score, toks[m.first].start)


def match_across(quote: str, text_a: str, text_b: str, repeated: set[str] | None = None) -> TextHit | None:
    """A quote that starts on page A and runs onto page B. Furniture (footer of A, header of B) is
    removed from both. Only an exact match is accepted across a page break, and only if at least
    FUZZY_MIN_TOKENS tokens of the quote are on page A."""
    ta, runs_a = page_stream(text_a, repeated)
    tb, runs_b = page_stream(text_b, repeated)
    q = cut_runs([t.text for t in tokenize(quote)], runs_a + runs_b)
    p = [t.text for t in ta] + [t.text for t in tb]
    m = find(q, p, fuzzy=False)
    if m is None or m.last < len(ta) or len(ta) - m.first < FUZZY_MIN_TOKENS:
        return None
    on_a = _span(text_a, ta, m.first, len(ta) - 1, "")
    rest = text_a[ta[-1].end:]
    on_a += rest[: len(rest) - len(rest.lstrip(",;:"))]  # the comma before the page break
    on_b = _span(text_b, tb, 0, m.last - len(ta), quote)
    return TextHit(f"{on_a} {on_b}", "normalised", 100.0, ta[m.first].start)


# ---- PDF words (boxes) ------------------------------------------------------------------------


def word_stream(words: list[tuple], repeated: set[str] | None = None
                ) -> tuple[list[tuple[Tok, int]], list[tuple[str, ...]]]:
    """PyMuPDF words (x0, y0, x1, y1, text, block, line, wno) -> [(token, word index)] with furniture
    lines removed, plus the furniture token runs. One word can give several tokens ("Vendor's")."""
    lines: dict[tuple[int, int], list[tuple[Tok, int]]] = {}
    for wi, w in enumerate(words):
        ln = lines.setdefault((w[5], w[6]), [])
        for t in tokenize(w[4]):
            ln.append((Tok(t.text, wi, wi, 0), wi))
    out: list[tuple[Tok, int]] = []
    runs: list[tuple[str, ...]] = []
    for ln in lines.values():  # dicts keep insertion order: the PDF's reading order
        key = " ".join(t.text for t, _ in ln)
        if key in (repeated or set()) or is_page_number(key):
            if len(ln) >= 2:
                runs.append(tuple(t.text for t, _ in ln))
            continue
        out.extend(ln)
    return out, runs


def match_words(quote: str, words: list[tuple], *, repeated: set[str] | None = None,
                prefix_ok: bool = False) -> list[int]:
    """Indices of the PDF words that hold `quote`; [] if it is not there. With `prefix_ok`, a quote
    that runs off the end of the page matches by its longest exact prefix of FUZZY_MIN_TOKENS or more."""
    stream, runs = word_stream(words, repeated)
    q = cut_runs([t.text for t in tokenize(quote)], runs)
    p = [t.text for t, _ in stream]
    m = find(q, p)
    if m is None and prefix_ok:
        for k in range(len(q) - 1, FUZZY_MIN_TOKENS - 1, -1):
            m = find(q[:k], p, fuzzy=False)
            if m is not None and m.last == len(p) - 1:
                break
            m = None
    if m is None:
        return []
    return sorted({wi for _, wi in stream[m.first:m.last + 1]})
