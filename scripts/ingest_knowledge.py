#!/usr/bin/env python3
"""Index the law and playbook markdown into the knowledge store.

    cd services/api && CLOUD=local LLM=fake uv run python ../../scripts/ingest_knowledge.py --target local
    cd services/api && uv run python ../../scripts/ingest_knowledge.py --target gcp      # after `pulumi up`

For every entry under knowledge/law and knowledge/playbook:
  1. deep links (law only): works out the link to the exact provision (SSO section anchor, or the LOM
     PDF with #page=N, found by searching the downloaded PDF) and writes services/api/app/data/deeplinks.json.
     A PDF that cannot be fetched keeps the plain URL and is reported.
  2. uploads the markdown to gs://<bucket>/kb/<kb_version>/<id>.md (local: <data dir>/knowledge/kb/...),
  3. embeds title + plain words + the first 1,500 characters of the text (768-d, gemini-embedding-2),
  4. upserts the store record {id, kind, jurisdiction, contract_type, clause_types, title, citation, plain,
     excerpt, url, source_url, gcs_uri, kb_version, content_hash, embedding}.
It is idempotent: an entry whose content hash is unchanged is skipped. It prints the counts.
`--target gcp` needs Google Cloud application-default credentials with write access to the bucket and Firestore.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "services" / "api"
sys.path.insert(0, str(API_DIR))

from app import deeplinks  # noqa: E402
from app.config import Settings  # noqa: E402
from app.knowledge import parse_frontmatter, sections, _section  # noqa: E402
from app.knowledge_store import LocalKnowledgeStore, docs_from_files, ingest  # noqa: E402

_pdf_cache: dict[str, bytes] = {}


def fetch_pdf(url: str) -> bytes:
    if url not in _pdf_cache:
        req = urllib.request.Request(url, headers={"User-Agent": "contract-copilot-ingest/1.0"})
        for attempt in range(4):  # the LOM site drops connections now and then
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    _pdf_cache[url] = r.read()
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))
    return _pdf_cache[url]


def find_page(url: str, text: str) -> int | None:
    return deeplinks.pdf_page_of(fetch_pdf(url), text)


def law_records(knowledge_dir: Path) -> list[dict]:
    out = []
    for path in sorted((knowledge_dir / "law").glob("*/*.md")):
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        out.append({"id": meta.get("id") or path.stem, "source_url": str(meta.get("source_url") or ""),
                    "citation": str(meta.get("citation") or ""), "text": _section(sections(body), "text")})
    return out


def update_deeplinks(knowledge_dir: Path, out_path: Path) -> None:
    previous = deeplinks.load_deeplinks(out_path)
    links, unresolved = deeplinks.build_deeplinks(law_records(knowledge_dir), find_page)
    merged = {**previous, **links}  # a failed fetch keeps the link from an earlier run
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(dict(sorted(merged.items())), indent=1) + "\n", encoding="utf-8")
    print(f"deep links: {len(links)} resolved, {len(unresolved)} kept as plain URL -> {out_path}")
    for eid, why in sorted(unresolved.items()):
        print(f"  plain URL kept: {eid}: {why}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["local", "gcp"], required=True)
    ap.add_argument("--kb-version", default=os.environ.get("KB_VERSION", "v1"))
    ap.add_argument("--knowledge-dir", type=Path, default=None)
    ap.add_argument("--skip-deeplinks", action="store_true", help="do not fetch the official PDFs")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    settings = Settings()
    kdir = args.knowledge_dir or settings.knowledge_dir
    if not args.skip_deeplinks:
        update_deeplinks(kdir, deeplinks.DEEPLINKS_PATH)

    if args.target == "gcp":
        from app.adapters.gcp import GeminiEmbedder
        from app.adapters.gcp_knowledge import FirestoreKnowledgeStore

        store, embedder = FirestoreKnowledgeStore(settings), GeminiEmbedder(settings)
        where = f"gs://{settings.knowledge_bucket} + Firestore `knowledge`"
    else:
        if settings.llm == "vertex":
            from app.adapters.gcp import GeminiEmbedder

            embedder = GeminiEmbedder(settings)
        else:
            from app.adapters.local import HashEmbedder

            embedder = HashEmbedder()
        root = settings.data_dir / "knowledge"
        store, where = LocalKnowledgeStore(root), str(root)
    docs = docs_from_files(kdir, embed_model=embedder.model)
    counts = ingest(store, docs, embedder, args.kb_version)
    print(f"knowledge {args.kb_version} -> {where} (embedder {embedder.model}): {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
