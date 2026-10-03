"""Short owner-scoped transactions and guarded publication."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from observability import traced
from .contracts import RevisionError, ScopeRequest
from .generate import config_key

SHEET_COLUMNS = "id, book_id, chapter_node_id, scope_kind, scope_key, version, source_title, scope_title, source_fingerprint, config_key, created_at"
JOB_COLUMNS = "id, book_id, chapter_node_id, scope_kind, scope_key, status, stage, source_title, scope_title, sheet_id, error_code, error_detail, cancellation_requested, created_at, updated_at"


def scope_request(row) -> ScopeRequest:
    return ScopeRequest(scope_kind=row["scope_kind"], book_id=row["book_id"], chapter_node_id=row["chapter_node_id"])


def read_sheet(db, owner, sheet_id, *, pdf=False):
    extra = ", pdf_bytes" if pdf else ", content, source_references, provenance"
    row = db.execute(f"select {SHEET_COLUMNS}{extra} from revision_sheets where id=%s and owner_id=%s",
                     (sheet_id, owner)).fetchone()
    if not row:
        raise RevisionError("not_found", "This revision sheet is not available.")
    return row


def read_job(db, owner, job_id):
    row = db.execute("select * from revision_sheet_jobs where id=%s and owner_id=%s", (job_id, owner)).fetchone()
    if not row:
        raise RevisionError("not_found", "This generation job is not available.")
    return row


@traced("revision_sheets.store.enqueue", flow="revision_sheet")
def enqueue(db, owner, source, request_key, *, regenerate=False):
    # Serialize all create requests for one owner, including idempotency keys
    # reused across scopes. This lock is held only for a short SQL transaction.
    db.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"revision:{owner}",))
    previous = db.execute("select * from revision_sheet_jobs where owner_id=%s and (request_key=%s or request_aliases ? %s)",
                          (owner, request_key, request_key)).fetchone()
    if previous:
        operation = previous["regenerate"] if previous["request_key"] == request_key else previous["request_aliases"][request_key]
        if previous["scope_key"] != source.request.key or operation != regenerate:
            raise RevisionError("idempotency_conflict", "That request key was already used for a different operation.")
        return {"job": public_job(previous)}
    if not regenerate:
        existing = db.execute(f"select {SHEET_COLUMNS} from revision_sheets where owner_id=%s and scope_key=%s and source_fingerprint=%s and config_key=%s order by version desc limit 1",
                              (owner, source.request.key, source.fingerprint, config_key())).fetchone()
        if existing:
            db.execute("update revision_sheet_jobs set request_aliases=request_aliases || %s where id=%s and owner_id=%s",
                       (Jsonb({request_key: regenerate}), existing["id"], owner))
            return {"sheet": existing}
    active = db.execute("select * from revision_sheet_jobs where owner_id=%s and scope_key=%s and status in ('queued','running')",
                        (owner, source.request.key)).fetchone()
    if active:
        db.execute("update revision_sheet_jobs set request_aliases=request_aliases || %s where id=%s",
                   (Jsonb({request_key: regenerate}), active["id"]))
        return {"job": public_job(active)}
    row = db.execute("""insert into revision_sheet_jobs
        (id, owner_id, book_id, chapter_node_id, scope_kind, scope_key, source_title, scope_title, source_fingerprint, config_key, request_key, regenerate)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
        (uuid4(), owner, source.request.book_id, source.request.chapter_node_id, source.request.scope_kind,
         source.request.key, source.title, source.scope_title, source.fingerprint, config_key(), request_key, regenerate)).fetchone()
    return {"job": public_job(row)}


def public_job(row):
    return {k.strip(): row[k.strip()] for k in JOB_COLUMNS.split(",")}


def list_sheets(db, owner, document_type):
    sheets = db.execute(f"""select distinct on (scope_key) {SHEET_COLUMNS} from revision_sheets
        where owner_id=%s and scope_kind=%s order by scope_key, version desc""",
        (owner, "paper" if document_type == "paper" else "chapter")).fetchall()
    jobs = db.execute(f"select {JOB_COLUMNS} from revision_sheet_jobs where owner_id=%s and scope_kind=%s order by created_at desc limit 40",
                      (owner, "paper" if document_type == "paper" else "chapter")).fetchall()
    return {"sheets": sorted(sheets, key=lambda r: r["created_at"], reverse=True), "jobs": jobs}


def claim(db, worker_id, *, owner=None, job_id=None):
    return db.execute("""update revision_sheet_jobs set status='running', stage='reading_source',
        lease_owner=%s, lease_expires_at=now()+interval '5 minutes', updated_at=now()
        where id=(select id from revision_sheet_jobs where status='queued'
            and (%s::uuid is null or owner_id=%s::uuid)
            and (%s::uuid is null or id=%s::uuid) order by created_at for update skip locked limit 1)
        returning *""", (worker_id, owner, owner, job_id, job_id)).fetchone()


def heartbeat(db, job_id, worker_id, stage=None):
    return bool(db.execute("""update revision_sheet_jobs set lease_expires_at=now()+interval '5 minutes',
        updated_at=now(), stage=coalesce(%s,stage) where id=%s and lease_owner=%s
        and status='running' and lease_expires_at>now() and not cancellation_requested""",
        (stage, job_id, worker_id)).rowcount)


def finish_failure(db, job_id, worker_id, code, detail):
    db.execute("""update revision_sheet_jobs set
        status=case when cancellation_requested then 'cancelled' else 'failed' end,
        stage='finished', error_code=%s,error_detail=%s,lease_owner=null,lease_expires_at=null,updated_at=now()
        where id=%s and lease_owner=%s and status='running'""",
        (code, detail[:1500], job_id, worker_id))


def cancel(db, owner, job_id):
    read_job(db, owner, job_id)
    db.execute("""update revision_sheet_jobs set cancellation_requested=true,
        status=case when status='queued' then 'cancelled' else status end, updated_at=now()
        where id=%s and owner_id=%s and status in ('queued','running')""", (job_id, owner))
    return public_job(read_job(db, owner, job_id))


def recover(db):
    # An interrupted model call may have been billed. Do not automatically
    # reset its repair allowance; surface an explicit, user-retryable failure.
    return db.execute("""update revision_sheet_jobs set
        status=case when cancellation_requested then 'cancelled' else 'failed' end,
        error_code='worker_interrupted', error_detail='Generation was interrupted. Retry to start a fresh attempt.',
        stage='finished',lease_owner=null,lease_expires_at=null,updated_at=now()
        where status='running' and lease_expires_at<now()""").rowcount


@traced("revision_sheets.store.publish", flow="revision_sheet")
def publish(db, job, worker_id, source, sheet, pdf, provenance):
    row = db.execute("select * from revision_sheet_jobs where id=%s for update", (job["id"],)).fetchone()
    if (not row or row["status"] != "running" or row["lease_owner"] != worker_id
            or row["cancellation_requested"]):
        raise RevisionError("cancelled", "Generation was cancelled or its lease was lost.")
    if not heartbeat(db, job["id"], worker_id):
        raise RevisionError("lease_lost", "Generation lease expired before publication.")
    if source.fingerprint != job["source_fingerprint"]:
        raise RevisionError("source_changed", "The source changed during generation. Regenerate against the current source.")
    version = db.execute("select coalesce(max(version),0)+1 as version from revision_sheets where owner_id=%s and scope_key=%s",
                         (job["owner_id"], job["scope_key"])).fetchone()["version"]
    db.execute("""insert into revision_sheets
        (id,owner_id,book_id,chapter_node_id,scope_kind,scope_key,version,source_title,scope_title,
         source_fingerprint,config_key,content,source_references,provenance,pdf_bytes)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (job["id"], job["owner_id"], job["book_id"], job["chapter_node_id"], job["scope_kind"], job["scope_key"],
         version, source.title, source.scope_title, source.fingerprint, job["config_key"],
         Jsonb(sheet.model_dump()), Jsonb(source.references), Jsonb(provenance), pdf))
    db.execute("""update revision_sheet_jobs set status='ready',stage='ready',sheet_id=%s,
        lease_owner=null,lease_expires_at=null,updated_at=now() where id=%s""", (job["id"], job["id"]))
