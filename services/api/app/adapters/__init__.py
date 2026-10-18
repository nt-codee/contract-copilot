"""Adapter selection: CLOUD=gcp|local picks storage and search; LLM=vertex|fake picks the model."""

from __future__ import annotations

from app.config import Settings
from app.knowledge import Knowledge
from app.pipeline import Services


def build_services(settings: Settings) -> Services:
    if settings.llm == "vertex":
        from app.adapters.gcp import GeminiEmbedder, GeminiLLM

        llm, embedder = GeminiLLM(settings), GeminiEmbedder(settings)
    elif settings.llm == "fake":
        from app.adapters.local import FakeLLM, HashEmbedder

        llm, embedder = FakeLLM(), HashEmbedder()
    else:
        raise RuntimeError(f"LLM must be 'vertex' or 'fake', got {settings.llm!r}")

    if settings.cloud == "gcp":
        from app.adapters.gcp import (FirestoreCompanyStore, FirestoreIndex, FirestoreReviewStore,
                                      GcsBlobStore)

        blobs, index, store = GcsBlobStore(settings), FirestoreIndex(settings), FirestoreReviewStore(settings)
        company = FirestoreCompanyStore(settings)
    elif settings.cloud == "local":
        from app.adapters.local import local_stores

        blobs, index, store, company = local_stores(settings)
    else:
        raise RuntimeError(f"CLOUD must be 'gcp' or 'local', got {settings.cloud!r}")

    kb = Knowledge.load(settings.knowledge_dir)
    return Services(settings=settings, llm=llm, embedder=embedder, blobs=blobs, index=index, store=store,
                    company=company, kb=kb)
