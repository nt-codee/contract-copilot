"""Routes for the knowledge library, workspace playbook overrides and the company policy PDF.

Mounted from main.py with one include_router line. Who is calling, and which workspace they work in, comes
from app.auth.get_actor like every other route: a demo visitor reads the demo workspace and gets
401 sign_in_required on a write; policy edits need an owner or admin of the workspace.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.auth import Actor, get_actor, require_policy_manager
from app.models import KnowledgeEntry
from app.pipeline import Services, UserError
from app.playbook_overrides import (AUDIT_REVERT, AUDIT_SET, NAME, PlaybookItem, PlaybookOverrideIn,
                                    audit_record, merged_items, override_ref)

router = APIRouter()
NAME_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _svc(request: Request) -> Services:
    return request.app.state.services


# ---- knowledge library ---------------------------------------------------------------------------


@router.get("/api/knowledge", response_model=list[KnowledgeEntry])
def list_knowledge(request: Request,
                   kind: str | None = Query(None, pattern="^(law|case|playbook)$"),
                   jurisdiction: str | None = Query(None, pattern="^(my|sg)$"),
                   contract_type: str | None = Query(None, max_length=40),
                   q: str = Query("", max_length=200)):
    return _svc(request).kb.entries(kind=kind, jurisdiction=jurisdiction, contract_type=contract_type, q=q)


# registered before the detail route: "{entry_id:path}" would otherwise swallow the "/raw" suffix
@router.get("/api/knowledge/{entry_id:path}/raw")
def get_knowledge_raw(request: Request, entry_id: str):
    stream = _svc(request).kb.raw(entry_id)
    if stream is None:
        raise UserError(404, "knowledge_not_found", "We could not find that entry.")
    return StreamingResponse(stream, media_type="text/markdown; charset=utf-8")


@router.get("/api/knowledge/{entry_id:path}", response_model=KnowledgeEntry)
def get_knowledge(request: Request, entry_id: str):
    entry = _svc(request).kb.entry(entry_id)
    if entry is None:
        raise UserError(404, "knowledge_not_found", "We could not find that entry.")
    return entry


# ---- your standard positions ---------------------------------------------------------------------


def _check_names(svc: Services, contract_type: str, clause_type: str) -> None:
    known = {ref.split("/", 1)[0] for ref in svc.kb.playbook}
    if contract_type not in known or not NAME.match(clause_type):
        raise UserError(404, "playbook_not_found", "We could not find that playbook entry.")


@router.get("/api/playbook", response_model=list[PlaybookItem])
def list_playbook(request: Request, contract_type: str | None = Query(None, max_length=40),
                  actor: Actor = Depends(get_actor)):
    svc = _svc(request)
    return merged_items(svc.kb, svc.overrides, actor.owner, contract_type)


@router.put("/api/playbook/{contract_type}/{clause_type}", response_model=PlaybookItem)
def put_playbook(request: Request, contract_type: str, clause_type: str, body: PlaybookOverrideIn,
                 actor: Actor = Depends(get_actor)):
    require_policy_manager(actor)  # demo visitor: 401 sign_in_required; plain member: 403 forbidden_role
    svc = _svc(request)
    _check_names(svc, contract_type, clause_type)
    body = body.cleaned()
    if not (body.standard or body.red_flags or body.ask):
        raise UserError(422, "empty_position", "Write at least your standard, a red flag or an ask.")
    svc.overrides.put(actor.owner, contract_type, clause_type, body, actor.audit_actor)
    audit_record(svc.audit, AUDIT_SET, actor=actor.audit_actor, workspace_id=actor.owner,
                 target_id=f"{contract_type}/{clause_type}")
    return next(i for i in merged_items(svc.kb, svc.overrides, actor.owner, contract_type)
                if i.id == override_ref(actor.owner, contract_type, clause_type))


@router.delete("/api/playbook/{contract_type}/{clause_type}", status_code=204)
def delete_playbook(request: Request, contract_type: str, clause_type: str, actor: Actor = Depends(get_actor)):
    require_policy_manager(actor)
    svc = _svc(request)
    _check_names(svc, contract_type, clause_type)
    removed = svc.overrides.delete(actor.owner, contract_type, clause_type)  # reverting twice is fine
    if removed:
        audit_record(svc.audit, AUDIT_REVERT, actor=actor.audit_actor, workspace_id=actor.owner,
                     target_id=f"{contract_type}/{clause_type}")
    return Response(status_code=204)


# ---- the stored policy PDF, so a citation can open the page it came from --------------------------


@router.get("/api/company/docs/{doc_id}/pdf")
def get_company_pdf(request: Request, doc_id: str, actor: Actor = Depends(get_actor)):
    svc = _svc(request)
    # workspace-scoped: another workspace's document id is a 404, exactly like a missing one
    if not NAME_ID.match(doc_id) or svc.company.get(doc_id, owner=actor.owner) is None:
        raise UserError(404, "company_doc_not_found", "We could not find that document.")
    data = svc.blobs.get(f"company/{doc_id}.pdf")
    if data is None:
        raise UserError(404, "company_doc_not_found", "The original PDF is no longer stored.")
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc_id}.pdf"'})
