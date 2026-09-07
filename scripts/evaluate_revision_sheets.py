"""Generate an inspectable revision artifact from an explicit local CLI scope.

This is a smoke/fit evaluation, not an automatic semantic-completeness score.
Artifacts contain source material and are written under ignored outputs/.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from storage.database import connection, environment_owner_id
from revision_sheets.contracts import ScopeRequest, Sheet, RevisionError
from revision_sheets import store
from revision_sheets.worker import RevisionWorker
from revision_sheets.generate import generate
from revision_sheets.source import load_source


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--chapter-node-id", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save", action="store_true", help="Save through the real job/worker path under DEFAULT_OWNER_ID; process only this job.")
    args = parser.parse_args()
    request = ScopeRequest(scope_kind="chapter" if args.chapter_node_id else "paper",
                           book_id=args.book_id, chapter_node_id=args.chapter_node_id)
    with connection(readonly=True) as db:
        source = load_source(db, owner_id=environment_owner_id(), request=request)
    args.output.mkdir(parents=True, exist_ok=True)
    print(f"Scope: {source.scope_title}; {len(source.units)} source units.", flush=True)
    if args.save:
        owner, worker_id = environment_owner_id(), f"revision-evaluation-{uuid4()}"
        with connection() as db:
            result = store.enqueue(db, owner, source, str(uuid4()))
            job = store.claim(db, worker_id, owner=owner, job_id=result["job"]["id"]) if "job" in result else None
        if "job" in result:
            if not job:
                raise RevisionError("already_running", "This scope is already being generated. Wait for its worker.")
            RevisionWorker(worker_id=worker_id).process(job)
            with connection(readonly=True) as db:
                finished = store.read_job(db, owner, job["id"])
            if finished["status"] != "ready":
                raise RevisionError(finished["error_code"], finished["error_detail"])
            sheet_id = finished["sheet_id"]
        else:
            sheet_id = result["sheet"]["id"]
        with connection(readonly=True) as db:
            row = store.read_sheet(db, owner, sheet_id)
            pdf = bytes(store.read_sheet(db, owner, sheet_id, pdf=True)["pdf_bytes"])
        sheet, provenance = Sheet.model_validate(row["content"]), {**row["provenance"], "sheet_id": str(sheet_id)}
    else:
        sheet, pdf, provenance = generate(source, progress=lambda s: print(s, flush=True),
            on_draft=lambda s: (args.output / "last-draft.json").write_text(s.model_dump_json(indent=2)),
            on_review=lambda r: (args.output / "last-review.json").write_text(r.model_dump_json(indent=2)))
    (args.output / "sheet.pdf").write_bytes(pdf)
    if provenance.get("html"):
        (args.output / "sheet.html").write_text(provenance["html"])
    (args.output / "sheet.json").write_text(sheet.model_dump_json(indent=2))
    (args.output / "source.json").write_text(json.dumps(source.references, indent=2))
    (args.output / "metrics.json").write_text(json.dumps({"scope": request.model_dump(),
        "source_title": source.title, "scope_title": source.scope_title,
        "source_fingerprint": source.fingerprint, "source_units": len(source.units),
        "pdf_bytes": len(pdf), "semantic_review": "automated rubric passed; human review still required", **provenance}, indent=2))
    print(f"Saved validated A4 artifact to {args.output}; automated review passed; inspect the pages before release.", flush=True)


if __name__ == "__main__":
    main()
