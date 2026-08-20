"""Run the source-first gold slice against the real application.

    uv run python -m scripts.evaluate_source_first --all
    uv run python -m scripts.evaluate_source_first --resolution-only --all

Needs a database with the gold set's book ingested, and — for everything except
`--resolution-only` — a model, because the rung a turn settles at is a property
of the turn actually running.

`--resolution-only` measures the deterministic half on its own: how many of the
gold selections match canonical text. That half needs no model and no cost, and
it is the measurement that says whether "highlight a sentence and ask about it"
works at all.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from evals.source_first import evaluate_source_first
from storage.database import connection as database_connection
from storage.database import environment_owner_id, parse_owner_id
from study.anchors import resolve_document_anchors
from study.contracts import parse_anchor
from study.conversation import execute_conversation_turn, new_conversation_state
from study.grounding import GroundingPolicy
from study.side_context import AnchoredSource, build_side_context

DEFAULT_GOLD = Path("evaluation/source_first_gold.json")


def _arguments():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--case", action="append")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--owner-id", help="Owner UUID; defaults to DEFAULT_OWNER_ID")
    parser.add_argument(
        "--book-id",
        type=int,
        help=(
            "The book to anchor against. Defaults to looking the gold set's "
            "title up in this database, because a book id is a property of one "
            "database rather than of the judgments."
        ),
    )
    parser.add_argument(
        "--library-book-id",
        type=int,
        action="append",
        help="Books the ladder may widen to. Repeat for several.",
    )
    parser.add_argument(
        "--resolution-only",
        action="store_true",
        help="Measure selection resolution without running any turn.",
    )
    return parser.parse_args()


def _select(dataset, args):
    cases = dataset["cases"]
    if args.all:
        return cases
    wanted = set(args.case or ())
    chosen = [case for case in cases if case["id"] in wanted]
    missing = wanted.difference({case["id"] for case in chosen})
    if missing:
        raise SystemExit(f"unknown case(s): {', '.join(sorted(missing))}")
    return chosen


def _book_id(dataset: dict, override: int | None, owner_id: UUID) -> int:
    """Which book in *this* database the gold set is about."""

    if override is not None:
        return override
    title = dataset["book"]["title"]
    with database_connection(readonly=True) as connection:
        row = connection.execute(
            """
            select id from books
            where owner_id = %s and title = %s and status = 'ready'
            order by id desc
            limit 1
            """,
            (owner_id, title),
        ).fetchone()
    if row is None:
        raise SystemExit(
            f"no ready book titled {title!r} for this owner. "
            "Ingest it, or pass --book-id."
        )
    return int(row["id"])


def _resolver(owner_id: UUID):
    def resolve(anchor: dict) -> tuple[bool, tuple[str, ...]]:
        with database_connection(readonly=True) as connection:
            (resolved,) = resolve_document_anchors(
                connection,
                [parse_anchor({**anchor, "anchor_id": "gold"})],
                owner_id=owner_id,
            )
        return resolved.matched, resolved.chunk_ids

    return resolve


def _runner(owner_id: UUID, library_book_ids):
    """Run one anchored turn exactly as a side chat on a reading session would."""

    def run(question: str, anchor: dict, *, stay_in_source: bool):
        book_id = anchor["book_id"]
        with database_connection(readonly=True) as connection:
            (resolved,) = resolve_document_anchors(
                connection,
                [parse_anchor({**anchor, "anchor_id": "gold"})],
                owner_id=owner_id,
            )
        library = list(library_book_ids or ()) + [book_id]
        result, _ = execute_conversation_turn(
            question,
            new_conversation_state(book_ids=library, conversation_id="gold"),
            owner_id=owner_id,
            book_ids=library,
            side_context=build_side_context([], [], sources=[resolved.as_source()]),
            grounding_policy=GroundingPolicy.for_source(
                [book_id],
                library_book_ids=library,
                allow_model_knowledge=not stay_in_source,
            ),
        )
        return result

    return run


def main() -> None:
    load_dotenv()
    args = _arguments()
    dataset = json.loads(args.gold.read_text())
    cases = _select(dataset, args)
    owner_id = parse_owner_id(args.owner_id or environment_owner_id())
    book_id = _book_id(dataset, args.book_id, owner_id)
    # The gold set names its book by title and records an id only as
    # provenance. Ingesting the same PDF into a different database gives it a
    # different id, and a judgment that silently resolves to nothing is worse
    # than one that refuses to run.
    cases = [
        {**case, "anchor": {**case["anchor"], "book_id": book_id}} for case in cases
    ]
    resolve = _resolver(owner_id)

    if args.resolution_only:
        rows = []
        for case in cases:
            matched, chunk_ids = resolve(case["anchor"])
            expected = case.get("expect_selection_resolves")
            rows.append(
                {
                    "id": case["id"],
                    "matched": matched,
                    "chunk_ids": list(chunk_ids),
                    "expected": expected,
                    "agrees": expected is None or matched is expected,
                }
            )
        judged = [row for row in rows if row["expected"] is not None]
        report = {
            "set_id": dataset["set_id"],
            "measured": "selection_resolution",
            "cases": len(rows),
            "resolution_rate": (
                sum(1 for row in rows if row["matched"]) / len(rows) if rows else None
            ),
            "agreement": (
                sum(1 for row in judged if row["agrees"]) / len(judged)
                if judged
                else None
            ),
            "rows": rows,
        }
    else:
        run = _runner(owner_id, args.library_book_id)
        summary = evaluate_source_first(cases, resolve=resolve, run_turn=run)
        report = {
            "set_id": dataset["set_id"],
            "measured": "anchored_turns",
            **{key: value for key, value in summary.items() if key != "rows"},
            "rows": [
                {
                    "id": row.case_id,
                    "checks": row.checks,
                    "failures": list(row.failures),
                    "error": row.error,
                    "answer": row.result.answer if row.result else None,
                }
                for row in summary["rows"]
            ],
        }

    report["ran_at"] = datetime.now(timezone.utc).isoformat()
    rendered = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.write_text(rendered + "\n")
        print(f"wrote {args.output}")
    else:
        print(rendered)

    # A blended answer is a defect rather than a low score, so the run says so
    # in its exit code and CI can treat it as one.
    if report.get("blended"):
        raise SystemExit(f"blended answers: {', '.join(report['blended'])}")


if __name__ == "__main__":
    main()
