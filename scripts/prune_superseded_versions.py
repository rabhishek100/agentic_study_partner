"""Reclaim derived data from ingestion versions nothing points at any more.

Run by a person, not by the worker. The retention sweeps that do run on a
timer are what deleted 52 book sources on 2026-09-02, and this one deletes
rows rather than objects: cascading a version removes its evidence, frames,
observations, regions and embeddings in one statement. That is the right
behaviour and the wrong thing to have happen unattended on a schedule.

Reports by default. Deleting requires --apply.

    python -m scripts.prune_superseded_versions
    python -m scripts.prune_superseded_versions --keep 0 --apply
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from storage.database import connection, resolve_database_url
from video.version_retention import prune_superseded_versions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument(
        "--keep",
        type=int,
        default=None,
        help="Superseded versions to keep per video for rollback (default 1)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually delete; otherwise report only"
    )
    return parser


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = build_parser().parse_args()

    url = arguments.database_url or resolve_database_url()
    with connection(url) as database:
        summary = prune_superseded_versions(
            database, keep=arguments.keep, dry_run=not arguments.apply
        )

    verb = "would remove" if summary.dry_run else "removed"
    print(
        f"{verb} {summary.versions_removed} superseded versions "
        f"({summary.evidence_units_removed} evidence units, "
        f"{summary.embeddings_removed} embeddings, "
        f"{summary.frames_removed} frames)"
    )
    print(
        f"kept {summary.versions_kept_for_rollback} for rollback and "
        f"{summary.versions_kept_for_citations} because an answer cites them"
    )
    if summary.dry_run and summary.versions_removed:
        print("nothing was deleted; pass --apply to act on this")
    return 0


if __name__ == "__main__":
    sys.exit(main())
