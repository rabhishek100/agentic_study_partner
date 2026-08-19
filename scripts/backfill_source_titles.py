"""Give books, papers, and lectures the names their content already states.

Everything ingested before `study.titles` existed kept whatever the pipeline
defaulted to, which for an uploaded lecture is the filename it arrived under
and for a paper is usually the arXiv identifier. The names they should have had
are already in the database: a book stores its PDF metadata in `metadata_json`,
and every source stores the filename to read as words.

Two rules, both about not doing damage:

Only machine-generated titles are replaced. If the stored title is anything
other than the filename, its stem, or a known placeholder, a person chose it,
and re-deriving over a reader's rename would undo their work every time this
heuristic changed.

Nothing is re-parsed. This reads what ingestion already stored. A paper whose
first-page title was never captured — because it was ingested before that
existed — is reported rather than guessed at, and re-ingesting it is the way to
get that title, not this script.

    uv run python -m scripts.backfill_source_titles            # report only
    uv run python -m scripts.backfill_source_titles --apply
"""

import argparse

from storage.database import connection as database_connection
from study.titles import looks_machine_generated, resolve_title


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rename books, papers, and lectures still carrying a filename or a "
            "placeholder as their title."
        )
    )
    parser.add_argument("--database-url", help="Postgres URL; defaults to DATABASE_URL")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the renames. Without this the script only reports them.",
    )
    return parser


def _rename_documents(connection, *, apply: bool) -> tuple[int, int]:
    """Books and papers, from the PDF metadata ingestion already stored."""

    rows = connection.execute(
        """
        select id, document_type, title, source_filename,
               metadata_json #>> '{pdf,title}' as embedded_title
        from public.books
        order by id
        """
    ).fetchall()

    considered = 0
    for row in rows:
        stored = str(row["title"] or "")
        filename = str(row["source_filename"] or "")
        if not looks_machine_generated(stored, filename):
            continue
        considered += 1
        derived = resolve_title(embedded=row["embedded_title"], filename=filename)
        if derived == stored:
            continue
        kind = row["document_type"]
        print(f"  {kind} {row['id']}: {stored!r} -> {derived!r}")
        if apply:
            connection.execute(
                "update public.books set title = %s where id = %s",
                (derived, row["id"]),
            )
    return considered, len(rows)


def _rename_lectures(connection, *, apply: bool) -> tuple[int, int]:
    """Lectures, from the container title the probe stored, else the filename.

    `media_metadata_json` holds the probe's own output, so a lecture ingested
    after the probe learned to read the container's title tag has an authored
    name available here without touching the media file.
    """

    rows = connection.execute(
        """
        select v.id, v.title, s.original_filename,
               s.media_metadata_json #>> '{embedded_title}' as embedded_title
        from video.videos as v
        join video.video_sources as s
          on s.video_id = v.id and s.owner_id = v.owner_id and s.is_primary
        order by v.created_at
        """
    ).fetchall()

    considered = 0
    for row in rows:
        stored = str(row["title"] or "")
        filename = str(row["original_filename"] or "")
        # A YouTube lecture has no filename to fall back to, so a placeholder
        # here means acquisition never finished; there is nothing to derive.
        if not filename and not row["embedded_title"]:
            continue
        if not looks_machine_generated(stored, filename):
            continue
        considered += 1
        derived = resolve_title(embedded=row["embedded_title"], filename=filename)
        if derived == stored:
            continue
        print(f"  lecture {row['id']}: {stored!r} -> {derived!r}")
        if apply:
            connection.execute(
                "update video.videos set title = %s where id = %s",
                (derived, row["id"]),
            )
    return considered, len(rows)


def main() -> None:
    args = build_argument_parser().parse_args()
    with database_connection(args.database_url) as connection:
        print("Books and papers:")
        documents, total_documents = _rename_documents(connection, apply=args.apply)
        print("Lectures:")
        lectures, total_lectures = _rename_lectures(connection, apply=args.apply)
        if not args.apply:
            connection.rollback()

    print(
        f"\n{documents} of {total_documents} documents and "
        f"{lectures} of {total_lectures} lectures carry a machine-generated title."
    )
    if not args.apply:
        print("Nothing was written. Re-run with --apply to keep these names.")


if __name__ == "__main__":
    main()
