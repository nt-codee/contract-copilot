"""Read-only knowledge the agent searches: law (knowledge/law) and the playbook (knowledge/playbook).

Files are read once at startup and kept in memory. No cloud SDK. Company documents are not here;
they are user data behind the CompanyStore port.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.deeplinks import load_deeplinks
from app.models import KnowledgeEntry

log = logging.getLogger("cc.knowledge")

TEXT_EXCERPT = 400
PLAYBOOK_EXCERPT = 1200
PLAIN_EXCERPT = 400
TOP_K = 4
CLAUSE_BOOST = 2.0
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was "
                  "which with shall may not any".split())


@dataclass(frozen=True)
class LawEntry:
    id: str
    jurisdiction: str
    kind: str  # statute | case
    title: str
    citation: str
    clause_types: tuple[str, ...]
    source_url: str
    text: str
    plain: str
    when: str = ""
    body: str = ""  # the whole markdown body, for the detail view
    deeplink: str = ""  # link to the exact provision (app/data/deeplinks.json), "" when unknown

    @property
    def link(self) -> str:
        """The best link to show: the deep link, else the plain source URL."""
        return self.deeplink or self.source_url


@dataclass(frozen=True)
class PlaybookEntry:
    ref: str  # "<contract_type>/<clause_type>"
    title: str
    text: str  # the file without its first heading: what the agent reads
    balanced: str  # the "What balanced looks like" section: what a citation quotes
    plain: str = ""


@dataclass
class Knowledge:
    law: list[LawEntry] = field(default_factory=list)
    playbook: dict[str, PlaybookEntry] = field(default_factory=dict)
    _tf: list[Counter] = field(default_factory=list, repr=False)
    _df: Counter = field(default_factory=Counter, repr=False)
    _avg_len: float = field(default=1.0, repr=False)

    # ---- loading --------------------------------------------------------------------------

    @classmethod
    def load(cls, knowledge_dir: Path) -> "Knowledge":
        links = load_deeplinks()
        k = cls(law=[replace(e, deeplink=links.get(e.id, "")) for e in load_law(knowledge_dir / "law")],
                playbook=load_playbook(knowledge_dir / "playbook"))
        k._build_index()
        log.info("knowledge loaded: %d law entries, %d playbook entries", len(k.law), len(k.playbook))
        return k

    def _build_index(self) -> None:
        self._tf = []
        self._df = Counter()
        for e in self.law:
            # title and citation count extra: they are the densest description of an entry
            toks = tokens(f"{e.title} {e.title} {e.citation} {e.citation} {e.text} {e.plain}")
            tf = Counter(toks)
            self._tf.append(tf)
            self._df.update(tf.keys())
        total = sum(sum(tf.values()) for tf in self._tf)
        self._avg_len = (total / len(self._tf)) if self._tf else 1.0

    # ---- search ---------------------------------------------------------------------------

    def search_law(self, query: str, jurisdiction: str, clause_type: str | None = None,
                   limit: int = TOP_K) -> list[LawEntry]:
        return [e for e, _ in self.search_law_scored(query, jurisdiction, clause_type, limit)]

    def search_law_scored(self, query: str, jurisdiction: str, clause_type: str | None = None,
                          limit: int = TOP_K) -> list[tuple[LawEntry, float]]:
        """BM25 over title, citation, text and plain words; x2 when the clause type matches."""
        q = tokens(query)
        n = len(self.law)
        scored: list[tuple[float, int]] = []
        for i, e in enumerate(self.law):
            if e.jurisdiction != jurisdiction:
                continue
            tf = self._tf[i]
            dl = sum(tf.values()) or 1
            score = 0.0
            for t in set(q):
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                score += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * dl / self._avg_len))
            matches = bool(clause_type) and clause_type in e.clause_types
            if matches:
                # a clause-type match alone is still worth showing when the words did not hit
                score = (score or 0.01) * CLAUSE_BOOST
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda t: (-t[0], self.law[t[1]].id))
        return [(self.law[i], s) for s, i in scored[:limit]]

    def playbook_entry(self, contract_type: str, clause_type: str) -> PlaybookEntry | None:
        return self.playbook.get(f"{contract_type}/{clause_type}")

    # ---- the library, as users browse it -------------------------------------------------------

    def entries(self, *, kind: str | None = None, jurisdiction: str | None = None,
                contract_type: str | None = None, q: str = "") -> list[KnowledgeEntry]:
        """Law, case and playbook entries without `text`, filtered; q keywords must all appear."""
        words = tokens(q)
        out: list[KnowledgeEntry] = []
        for e in self.law:
            ke = _law_entry(e, with_text=False)
            if _wanted(ke, kind, jurisdiction, contract_type) and _has_words(
                    words, f"{e.title} {e.citation} {e.plain} {e.text} {' '.join(e.clause_types)}"):
                out.append(ke)
        for e in self.playbook.values():
            ke = _playbook_entry(e, with_text=False)
            if _wanted(ke, kind, jurisdiction, contract_type) and _has_words(
                    words, f"{e.title} {e.plain} {e.text} {ke.clause_types[0]}"):
                out.append(ke)
        order = {"law": 0, "case": 1, "playbook": 2}
        out.sort(key=lambda k: (order[k.kind], k.jurisdiction or "", k.contract_type or "", k.title))
        return out

    def entry(self, entry_id: str) -> KnowledgeEntry | None:
        if entry_id.startswith("playbook:"):
            e = self.playbook.get(entry_id[len("playbook:"):])
            return _playbook_entry(e, with_text=True) if e else None
        e = self.law_by_id(entry_id)
        return _law_entry(e, with_text=True) if e else None

    def law_by_id(self, ref: str) -> LawEntry | None:
        return next((e for e in self.law if e.id == ref), None)


def _law_entry(e: LawEntry, *, with_text: bool) -> KnowledgeEntry:
    return KnowledgeEntry(id=e.id, kind="case" if e.kind == "case" else "law", jurisdiction=e.jurisdiction,
                          title=e.title, citation=e.citation or None, clause_types=list(e.clause_types),
                          plain=e.plain or None, text=e.body if with_text else None, url=e.source_url or None,
                          source_url=e.link or None)


def _playbook_entry(e: PlaybookEntry, *, with_text: bool) -> KnowledgeEntry:
    ctype, clause = e.ref.split("/", 1)
    return KnowledgeEntry(id=f"playbook:{e.ref}", kind="playbook", contract_type=ctype, title=e.title,
                          clause_types=[clause], plain=e.plain or None, text=e.text if with_text else None)


def _wanted(k: KnowledgeEntry, kind: str | None, jurisdiction: str | None, contract_type: str | None) -> bool:
    return ((not kind or k.kind == kind) and (not jurisdiction or k.jurisdiction == jurisdiction)
            and (not contract_type or k.contract_type == contract_type))


def _has_words(words: list[str], haystack: str) -> bool:
    have = set(tokens(haystack))
    return all(w in have or any(h.startswith(w) for h in have) for w in words)


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


# ---- parsing ------------------------------------------------------------------------------


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Minimal `---` frontmatter: `key: value`, `key: [a, b]`, or `key:` followed by `- item` lines."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    meta: dict = {}
    key: str | None = None
    for line in raw[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key is not None:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(_scalar(line.lstrip()[2:]))
            continue
        m = re.match(r"([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = [_scalar(v) for v in val[1:-1].split(",") if v.strip()]
        elif val == "":
            meta[key] = []
        else:
            meta[key] = _scalar(val)
    return meta, raw[end + 4:].lstrip("\n")


def _scalar(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def sections(body: str) -> dict[str, str]:
    """Split a markdown body on `## ` headings; keys are lower-cased heading text."""
    out: dict[str, str] = {}
    name = ""
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if name:
                out[name] = "\n".join(buf).strip()
            name, buf = line[3:].strip().lower(), []
        elif name:
            buf.append(line)
    if name:
        out[name] = "\n".join(buf).strip()
    return out


def _section(secs: dict[str, str], *prefixes: str) -> str:
    for name, body in secs.items():
        if any(name.startswith(p) for p in prefixes):
            return body
    return ""


def clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def load_law(law_dir: Path) -> list[LawEntry]:
    entries: list[LawEntry] = []
    for path in sorted(law_dir.glob("*/*.md")) if law_dir.is_dir() else []:
        try:
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            secs = sections(body)
            text = _section(secs, "text")
            jur = str(meta.get("jurisdiction") or path.parent.name)
            if not text or jur not in ("my", "sg"):
                log.warning("skipping %s: no Text section or bad jurisdiction", path)
                continue
            ct = meta.get("clause_types") or []
            entries.append(LawEntry(
                id=str(meta.get("id") or path.stem), jurisdiction=jur, kind=str(meta.get("kind") or "statute"),
                title=str(meta.get("title") or path.stem), citation=str(meta.get("citation") or ""),
                clause_types=tuple(ct if isinstance(ct, list) else [ct]),
                source_url=str(meta.get("source_url") or ""), text=text,
                plain=_section(secs, "in plain words"), when=_section(secs, "when it matters"),
                body=body.strip()))
        except Exception:  # one bad file must not take the agent down
            log.exception("could not load law entry %s", path)
    return entries


def load_playbook(baseline_dir: Path) -> dict[str, PlaybookEntry]:
    out: dict[str, PlaybookEntry] = {}
    index = baseline_dir / "index.json"
    if index.is_file():
        files = [f for group in json.loads(index.read_text(encoding="utf-8")).values() for f in group]
    else:
        files = [f"{p.parent.name}/{p.name}" for p in sorted(baseline_dir.glob("*/*.md"))]
    for rel in files:
        path = baseline_dir / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("# ")), path.stem)
        body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("# ")).strip()
        balanced = _section(sections(text), "what balanced looks like") or body
        ref = f"{path.parent.name}/{path.stem}"
        out[ref] = PlaybookEntry(ref=ref, title=title, text=body, balanced=balanced,
                                 plain=_section(sections(text), "in plain words"))
    return out
