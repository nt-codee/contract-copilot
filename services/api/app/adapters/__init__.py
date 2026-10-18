"""Adapter selection: CLOUD=gcp|local picks storage and search; LLM=vertex|fake picks the model."""

from __future__ import annotations

from app import provenance
from app.audit import Audit
from app.config import Settings
from app.knowledge import Knowledge
from app.knowledge_store import HybridKnowledge, LocalKnowledgeStore
from app.playbook_overrides import MemoryPlaybookOverrides
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
        from app.adapters.gcp import (IdentityPlatformVerifier, FirestoreAuditLog, FirestoreCompanyStore,
                                      FirestoreIndex, FirestoreReviewStore, FirestoreUserStore,
                                      FirestoreWorkspaceStore, GcsBlobStore)

        blobs, index, store = GcsBlobStore(settings), FirestoreIndex(settings), FirestoreReviewStore(settings)
        company, auth = FirestoreCompanyStore(settings), IdentityPlatformVerifier(settings)
        users, audit_log = FirestoreUserStore(settings), FirestoreAuditLog(settings)
        workspaces = FirestoreWorkspaceStore(settings)
        from app.adapters.gcp_knowledge import FirestoreKnowledgeStore, FirestorePlaybookOverrides

        kstore, overrides = FirestoreKnowledgeStore(settings), FirestorePlaybookOverrides(settings)
        from app.adapters.gcp import FirestoreSpendCounter

        spend = FirestoreSpendCounter(settings)
        from app.adapters.gcp_budget import FirestoreBudgetLedger
        budget_ledger = FirestoreBudgetLedger(settings)
    elif settings.cloud == "local":
        from app.adapters.local import local_stores

        blobs, index, store, company, auth, users, audit_log, workspaces = local_stores(settings)
        kstore, overrides = LocalKnowledgeStore(settings.data_dir / "knowledge"), MemoryPlaybookOverrides()
        from app.adapters.local import MemorySpendCounter

        spend = MemorySpendCounter()
        from app.adapters.local import MemoryBudgetLedger
        budget_ledger = MemoryBudgetLedger()
    else:
        raise RuntimeError(f"CLOUD must be 'gcp' or 'local', got {settings.cloud!r}")

    kb = HybridKnowledge(Knowledge.load(settings.knowledge_dir), kstore, embedder)
    return Services(settings=settings, llm=llm, embedder=embedder, blobs=blobs, index=index, store=store,
                    company=company, kb=kb, auth=auth, users=users, audit=Audit(audit_log), workspaces=workspaces,
                    kb_version=provenance.kb_version(settings.knowledge_dir),  # once, at startup
                    prompt_version=provenance.prompt_version(), overrides=overrides, spend=spend,
                    budget_ledger=budget_ledger)
