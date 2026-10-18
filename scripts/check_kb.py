#!/usr/bin/env python3
"""Validate the legal knowledge base under knowledge/law.

Checks:
  - every entry file knowledge/law/<jurisdiction>/<id>.md has YAML frontmatter with the
    required fields, of the right kind and value;
  - clause_types are all in the ClauseType enum of shared/contracts/review.schema.json;
  - source_url is present and https;
  - the body has non-empty "## Text", "## In plain words" and
    "## When it matters in an NDA or service agreement" sections;
  - knowledge/law/index.json lists exactly the entry files, and its fields match the frontmatter;
  - at least MIN_PER_JURISDICTION entries per jurisdiction.

Standard library only. Uses PyYAML for the frontmatter if it is installed, otherwise a small
parser for the flat `key: value` / `key: [a, b]` form these files use.

Usage: python3 scripts/check_kb.py      (exit 0 = all good, 1 = problems found)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "knowledge"  # index.json "file" paths are relative to this
LAW = KB / "law"
INDEX = LAW / "index.json"
SCHEMA = ROOT / "shared" / "contracts" / "review.schema.json"

JURISDICTIONS = {"my", "sg"}
KINDS = {"statute", "case"}
RETRIEVED = "2026-10-18"
MIN_PER_JURISDICTION = 12
REQUIRED = ["id", "jurisdiction", "kind", "title", "citation", "clause_types",
            "source_url", "retrieved", "licence_note"]
INDEX_FIELDS = ["id", "jurisdiction", "kind", "title", "citation", "clause_types", "file"]
SECTIONS = ["Text", "In plain words", "When it matters in an NDA or service agreement"]
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load_clause_types() -> set[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return set(schema["$defs"]["ClauseType"]["enum"])


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _mini_yaml(text: str) -> dict:
    """Parse flat `key: value` lines, with `[a, b]` lists. Enough for these files."""
    data: dict = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line or line[0].isspace():
            raise ValueError(f"frontmatter line {n} is not a flat 'key: value' pair: {line!r}")
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key.strip()] = [_unquote(v) for v in inner.split(",")] if inner else []
        else:
            data[key.strip()] = _unquote(value)
    return data


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("file does not start with '---' frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter is not closed with '---'")
    raw, body = text[4:end], text[end + 5:]
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(raw) or {}
    except ImportError:
        data = _mini_yaml(raw)
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    # PyYAML turns 2026-10-18 into a date; compare as text.
    if "retrieved" in data:
        data["retrieved"] = str(data["retrieved"])
    return data, body


def sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1)
            out[current] = ""
        elif current is not None:
            out[current] += line + "\n"
    return out


def check_entry(path: Path, clause_enum: set[str], errors: list[str]) -> dict | None:
    rel = path.relative_to(KB).as_posix()
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        errors.append(f"{rel}: {exc}")
        return None

    def err(msg: str) -> None:
        errors.append(f"{rel}: {msg}")

    for field in REQUIRED:
        if field not in meta or meta[field] in (None, "", []):
            err(f"missing or empty field '{field}'")
    if any(f not in meta for f in REQUIRED):
        return None

    for field in ["id", "jurisdiction", "kind", "title", "citation", "source_url", "licence_note"]:
        if not isinstance(meta[field], str):
            err(f"'{field}' must be a string")
    if not isinstance(meta["clause_types"], list):
        err("'clause_types' must be a list")
        meta["clause_types"] = []

    if not ID_RE.match(str(meta["id"])):
        err(f"id '{meta['id']}' must be lowercase words joined by '-'")
    if path.stem != meta["id"]:
        err(f"file name '{path.name}' does not match id '{meta['id']}'")
    if meta["jurisdiction"] not in JURISDICTIONS:
        err(f"jurisdiction '{meta['jurisdiction']}' not in {sorted(JURISDICTIONS)}")
    if path.parent.name != meta["jurisdiction"]:
        err(f"file is under '{path.parent.name}/' but jurisdiction is '{meta['jurisdiction']}'")
    if not str(meta["id"]).startswith(f"{meta['jurisdiction']}-"):
        err(f"id should start with '{meta['jurisdiction']}-'")
    if meta["kind"] not in KINDS:
        err(f"kind '{meta['kind']}' not in {sorted(KINDS)}")
    if meta["retrieved"] != RETRIEVED:
        err(f"retrieved is '{meta['retrieved']}', expected {RETRIEVED}")

    bad = [c for c in meta["clause_types"] if c not in clause_enum]
    if bad:
        err(f"clause_types not in ClauseType enum: {bad}")
    if len(set(meta["clause_types"])) != len(meta["clause_types"]):
        err("clause_types has duplicates")

    url = str(meta["source_url"]).strip()
    if not re.match(r"^https://[^\s/]+\.[^\s/]+/\S*$", url):
        err(f"source_url must be an https URL with a host and no spaces: '{url}'")

    found = sections(body)
    for name in SECTIONS:
        if name not in found:
            err(f"missing section '## {name}'")
        elif not found[name].strip():
            err(f"section '## {name}' is empty")
    if "Text" in found and kind_needs_quote(meta["kind"]) and '"' not in found["Text"] and "“" not in found["Text"]:
        err("Text section has no quoted words")

    meta["_file"] = rel
    return meta


def kind_needs_quote(kind: str) -> bool:
    # Statutes are quoted verbatim; cases carry a short quoted holding. Both need a quote.
    return kind in KINDS


def check_index(entries: dict[str, dict], errors: list[str]) -> None:
    try:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"index.json: cannot read: {exc}")
        return
    if not isinstance(index, list):
        errors.append("index.json: must be a JSON list")
        return
    seen: set[str] = set()
    for i, item in enumerate(index):
        where = f"index.json[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        missing = [f for f in INDEX_FIELDS if f not in item]
        extra = [f for f in item if f not in INDEX_FIELDS]
        if missing:
            errors.append(f"{where}: missing fields {missing}")
            continue
        if extra:
            errors.append(f"{where}: unexpected fields {extra}")
        eid = item["id"]
        if eid in seen:
            errors.append(f"{where}: duplicate id '{eid}'")
        seen.add(eid)
        meta = entries.get(eid)
        if meta is None:
            errors.append(f"{where}: id '{eid}' has no valid entry file")
            continue
        if item["file"] != meta["_file"]:
            errors.append(f"{where}: file '{item['file']}' but entry is at '{meta['_file']}'")
        for f in INDEX_FIELDS:
            if f != "file" and item[f] != meta[f]:
                errors.append(f"{where} ({eid}): '{f}' differs from frontmatter")
    for eid in entries:
        if eid not in seen:
            errors.append(f"index.json: entry '{eid}' is not listed")


def main() -> int:
    errors: list[str] = []
    clause_enum = load_clause_types()
    files = sorted(LAW.glob("*/*.md"))
    stray = [p for p in LAW.rglob("*.md") if p not in files and p.name != "README.md"]
    for p in stray:
        errors.append(f"{p.relative_to(KB).as_posix()}: entry files must sit at law/<jurisdiction>/<id>.md")

    entries: dict[str, dict] = {}
    for path in files:
        meta = check_entry(path, clause_enum, errors)
        if meta is None:
            continue
        if meta["id"] in entries:
            errors.append(f"{meta['_file']}: duplicate id '{meta['id']}'")
        entries[meta["id"]] = meta

    check_index(entries, errors)

    counts = {j: 0 for j in sorted(JURISDICTIONS)}
    kinds = {j: {k: 0 for k in sorted(KINDS)} for j in sorted(JURISDICTIONS)}
    for meta in entries.values():
        if meta["jurisdiction"] in counts:
            counts[meta["jurisdiction"]] += 1
            if meta["kind"] in KINDS:
                kinds[meta["jurisdiction"]][meta["kind"]] += 1
    for j, n in counts.items():
        if n < MIN_PER_JURISDICTION:
            errors.append(f"jurisdiction '{j}' has {n} entries, need at least {MIN_PER_JURISDICTION}")

    print(f"checked {len(files)} entry files and {INDEX.relative_to(ROOT)}")
    for j in counts:
        print(f"  {j}: {counts[j]} entries ({kinds[j]['statute']} statute, {kinds[j]['case']} case)")
    if errors:
        print(f"FAIL: {len(errors)} problem(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
