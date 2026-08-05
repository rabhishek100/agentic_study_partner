"""Name video conversations left holding the placeholder title.

Video conversations are created before their first question is answered, so
until the first turn learned to replace it, every one of them kept the name it
was born with. The question is still stored on the turn, so the name they
should have had is derivable rather than lost.

Renames only. A conversation with no turns has nothing to derive a name from
and is reported rather than touched, because deleting a reader's conversation
to tidy a list is not this script's decision to make.
"""

import argparse

from storage.database import connection as database_connection
from video.conversation_store import PLACEHOLDER_TITLE, derive_title


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rename video conversations still titled "
            f"{PLACEHOLDER_TITLE!r} after their first question."
        )
    )
    parser.add_argument(
        "--database-url",
        help="Postgres URL; defaults to DATABASE_URL",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the renames. Without this the script only reports them.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()

    with database_connection(args.database_url) as connection:
        rows = connection.execute(
            """
            select conversation.id, conversation.owner_id, first_turn.question
            from video.conversations as conversation
            left join lateral (
                select question from video.conversation_turns
                where conversation_id = conversation.id
                order by turn_index
                limit 1
            ) as first_turn on true
            where conversation.title = %s
            order by conversation.created_at
            """,
            (PLACEHOLDER_TITLE,),
        ).fetchall()

        renamed = 0
        skipped = 0
        for row in rows:
            if not row["question"]:
                skipped += 1
                print(f"skip  {row['id']}  (no turns; nothing to name it after)")
                continue
            title = derive_title(row["question"])
            print(f"{'rename' if args.apply else 'would'}  {row['id']}  ->  {title}")
            if args.apply:
                # Titled only while still holding the placeholder, so a
                # rename that lands between the read and the write is not
                # overwritten by this pass.
                renamed += connection.execute(
                    """
                    update video.conversations
                    set title = %s, updated_at = now()
                    where id = %s and owner_id = %s and title = %s
                    """,
                    (title, row["id"], row["owner_id"], PLACEHOLDER_TITLE),
                ).rowcount

    print(
        f"\n{len(rows)} placeholder conversations: "
        f"{renamed if args.apply else 0} renamed, {skipped} left alone"
        + ("" if args.apply else " (dry run; pass --apply to write)")
    )


if __name__ == "__main__":
    main()
