"""Revision jobs share the existing worker; model calls hold no DB connection."""

import logging
import threading

from storage.database import connection
from . import store
from .contracts import RevisionError
from .provider_errors import spend_refusal
from .generate import config_key, generate
from .source import load_source

logger = logging.getLogger("study_partner.revision_worker")


class RevisionWorker:
    def __init__(self, *, worker_id, database_url=None):
        self.worker_id, self.database_url = worker_id, database_url

    def claim(self):
        with connection(self.database_url) as db:
            return store.claim(db, self.worker_id)

    def recover_abandoned_jobs(self):
        with connection(self.database_url) as db:
            return store.recover(db)

    def process(self, job, *, model=None, images=None):
        stop = threading.Event()
        lost = threading.Event()

        def progress(stage=None):
            if lost.is_set():
                raise RevisionError("lease_lost", "Generation lease was lost.")
            with connection(self.database_url) as db:
                if not store.heartbeat(db, job["id"], self.worker_id, stage):
                    lost.set()
                    raise RevisionError("cancelled", "Generation was cancelled or its lease was lost.")

        def renew():
            while not stop.wait(45):
                try:
                    progress()
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=renew, daemon=True, name=f"revision-{job['id']}")
        thread.start()
        try:
            progress("reading_source")
            if job["config_key"] != config_key():
                raise RevisionError("config_changed", "Generation settings changed. Retry with the current configuration.")
            request = store.scope_request(job)
            with connection(self.database_url) as db:
                source = load_source(db, owner_id=job["owner_id"], request=request)
            if source.fingerprint != job["source_fingerprint"]:
                raise RevisionError("source_changed", "The source changed after this job was queued. Retry with the current source.")
            sheet, pdf, provenance = generate(source, model=model, progress=progress, images=images, job_id=str(job["id"]))
            progress("publishing")
            with connection(self.database_url) as db:
                # Lock the document against deletion/replacement while checking
                # freshness and committing the complete artifact transaction.
                db.execute("select id from books where id=%s and owner_id=%s for update", (job["book_id"], job["owner_id"]))
                current = load_source(db, owner_id=job["owner_id"], request=request)
                store.publish(db, job, self.worker_id, current, sheet, pdf, provenance)
        except Exception as error:
            refusal = None if isinstance(error, RevisionError) else spend_refusal(error)
            if isinstance(error, RevisionError):
                code, detail = error.code, str(error)
            elif refusal:
                # Distinct from a failure, because retrying cannot fix it. The
                # previous wording named the worker and the connection — the
                # two things that were working — and sent readers round a retry
                # loop while the account's monthly key limit stayed exhausted.
                code = "provider_quota_exhausted"
                detail = (
                    f"{refusal} Nothing was generated and this attempt cost nothing. "
                    "Raise the limit or add credit on the provider account, then create "
                    "the sheet again."
                )
            else:
                code = "generation_failed"
                detail = "Generation could not finish. Check the worker/provider connection and retry."
            logger.warning("revision generation failed", extra={"job_id": str(job["id"]), "error_code": code}, exc_info=not isinstance(error, RevisionError))
            with connection(self.database_url) as db:
                store.finish_failure(db, job["id"], self.worker_id, code, detail)
        finally:
            stop.set()
            thread.join(timeout=5)
