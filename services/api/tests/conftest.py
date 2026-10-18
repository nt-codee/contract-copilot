from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

os.environ["CLOUD"] = "local"
os.environ["LLM"] = "fake"

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import build_services  # noqa: E402
from app.config import REPO_ROOT, Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from pdfgen import make_pdf  # noqa: E402


def make_knowledge_dir(root: Path) -> Path:
    """Two law fixtures per jurisdiction plus the real playbook, in the layout the app expects."""
    k = root / "knowledge"
    shutil.copytree(Path(__file__).parent / "fixtures" / "knowledge" / "law", k / "law")
    shutil.copytree(REPO_ROOT / "knowledge" / "playbook", k / "playbook")
    return k


@pytest.fixture
def env(tmp_path, monkeypatch):
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "demo-01.pdf").write_bytes(make_pdf())
    (samples / "index.json").write_text(json.dumps([
        {"id": "demo-01", "title": "Demo services agreement", "contract_type": "service_agreement",
         "language": "en", "file": "demo-01.pdf"},
    ]))
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>cc</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SAMPLES_DIR", str(samples))
    monkeypatch.setenv("WEB_DIST", str(dist))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(make_knowledge_dir(tmp_path)))
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "10")
    return tmp_path


@pytest.fixture
def services(env):
    return build_services(Settings())


@pytest.fixture
def client(services):
    return TestClient(create_app(services.settings, services))


@pytest.fixture
def pdf_bytes():
    return make_pdf()
