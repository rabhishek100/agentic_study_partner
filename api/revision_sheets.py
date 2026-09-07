"""Authenticated revision artifacts and durable generation commands."""

from hashlib import sha256
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from api.auth import current_owner
from storage.database import connection
from revision_sheets import store
from revision_sheets.ask import Question, ask
from revision_sheets.contracts import RevisionError, ScopeRequest, Sheet
from revision_sheets.generate import config_key
from revision_sheets.render import diagram_layout
from revision_sheets.source import load_source

router = APIRouter(prefix="/api", tags=["revision sheets"])


async def run(operation):
    try:
        return await run_in_threadpool(operation)
    except RevisionError as error:
        code = {"not_found": 404, "source_unavailable": 404, "idempotency_conflict": 409}.get(error.code, 422)
        raise HTTPException(code, detail=str(error)) from error


@router.get("/revision-sheets")
async def listing(document_type: Literal["book", "paper"] = "book", owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            return store.list_sheets(db, owner, document_type)
    return await run(operation)


@router.post("/revision-sheets")
async def create(request: ScopeRequest, response: Response,
                 idempotency_key: str = Header(min_length=1, max_length=128), owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            source = load_source(db, owner_id=owner, request=request)
            return store.enqueue(db, owner, source, idempotency_key)
    result = await run(operation)
    response.status_code = 200 if "sheet" in result else 202
    return result


@router.get("/revision-sheets/{sheet_id}")
async def detail(sheet_id: UUID, owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            row = store.read_sheet(db, owner, sheet_id)
            try:
                source = load_source(db, owner_id=owner, request=store.scope_request(row))
                row["source_changed"] = source.fingerprint != row["source_fingerprint"]
            except RevisionError:
                row["source_changed"] = True
            row["settings_changed"] = row["config_key"] != config_key()
            row["diagram_layout"] = ({"width": 520, "height": 90, "nodes": [], "edges": []}
                if row["provenance"].get("html") else diagram_layout(Sheet.model_validate(row["content"])))
            return row
    return await run(operation)


@router.get("/revision-sheets/{sheet_id}/pdf")
async def pdf(sheet_id: UUID, owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            return bytes(store.read_sheet(db, owner, sheet_id, pdf=True)["pdf_bytes"])
    payload = await run(operation)
    return Response(payload, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="revision-{sheet_id}.pdf"',
        "Cache-Control": "private, max-age=86400, immutable", "ETag": f'"{sha256(payload).hexdigest()}"'})


@router.post("/revision-sheets/{sheet_id}/regenerate", status_code=202)
async def regenerate(sheet_id: UUID, idempotency_key: str = Header(min_length=1, max_length=128),
                     owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            row = store.read_sheet(db, owner, sheet_id)
            source = load_source(db, owner_id=owner, request=store.scope_request(row))
            return store.enqueue(db, owner, source, idempotency_key, regenerate=True)
    return await run(operation)


@router.get("/revision-sheet-jobs/{job_id}")
async def job(job_id: UUID, owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            return store.public_job(store.read_job(db, owner, job_id))
    return await run(operation)


@router.post("/revision-sheet-jobs/{job_id}/cancel")
async def cancel(job_id: UUID, owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            return store.cancel(db, owner, job_id)
    return await run(operation)


@router.post("/revision-sheet-jobs/{job_id}/retry", status_code=202)
async def retry(job_id: UUID, idempotency_key: str = Header(min_length=1, max_length=128),
                owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            row = store.read_job(db, owner, job_id)
            if row["status"] not in ("failed", "cancelled"):
                raise RevisionError("invalid_state", "Only failed or cancelled generation can be retried.")
            source = load_source(db, owner_id=owner, request=store.scope_request(row))
            return store.enqueue(db, owner, source, idempotency_key, regenerate=True)
    return await run(operation)


@router.post("/revision-sheets/{sheet_id}/ask")
async def followup(sheet_id: UUID, request: Question, owner: UUID = Depends(current_owner)):
    def operation():
        with connection() as db:
            row = store.read_sheet(db, owner, sheet_id)
            source = load_source(db, owner_id=owner, request=store.scope_request(row))
        return ask(source, request.question, sheet_id=str(sheet_id))
    return await run(operation)
