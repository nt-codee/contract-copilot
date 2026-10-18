"""Where a review came from: which model, which knowledge base, which prompts, which build.

`kb_version` and `prompt_version` are content hashes, so the same inputs always give the same string
and any edit to the knowledge base or the prompts gives a new one. No cloud SDK.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app import prompts
from app.models import Provenance, ReviewOptions

HEX = 12


def kb_version(knowledge_dir: Path) -> str:
    """sha256 over every file under `knowledge_dir` (relative path + bytes, sorted), first 12 hex.

    Dotfiles (.DS_Store and the like) are skipped so the same content hashes the same on any machine.
    A missing directory hashes as empty rather than failing startup.
    """
    h = hashlib.sha256()
    root = Path(knowledge_dir)
    if root.is_dir():
        for p in sorted(q for q in root.rglob("*") if q.is_file()):
            rel = p.relative_to(root)
            if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
                continue
            h.update(rel.as_posix().encode("utf-8") + b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:HEX]


def prompt_version() -> str:
    """sha256 over every string template in prompts.py (the UPPER_CASE str constants), first 12 hex."""
    h = hashlib.sha256()
    for name in sorted(n for n in vars(prompts) if n.isupper() and isinstance(getattr(prompts, n), str)):
        h.update(name.encode("utf-8") + b"\0" + getattr(prompts, name).encode("utf-8") + b"\0")
    return h.hexdigest()[:HEX]


def content_hash(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def build(model: str, kb: str, prompt: str, app_version: str | None, options: ReviewOptions | None = None) -> Provenance:
    return Provenance(model=model, kb_version=kb, prompt_version=prompt, app_version=app_version or None,
                      options=options)
