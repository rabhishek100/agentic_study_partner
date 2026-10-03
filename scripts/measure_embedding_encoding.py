"""Measure what a vector encoding costs, on the frozen gold sets.

Point it at a database whose embeddings are already in the encoding you want
to judge — a clone, a branch, or a scratch copy — and it reports what the gold
sets still recall through the real retrievers.

    # what today's encoding recalls
    python -m scripts.measure_embedding_encoding --dimension 1024

    # judge a candidate encoding without touching the corpus
    createdb candidate && pg_restore -d candidate corpus.dump
    psql candidate -c "alter table video.evidence_embeddings
                       alter column embedding type halfvec using embedding::halfvec"
    python -m scripts.measure_embedding_encoding \
        --database-url postgresql://.../candidate --dimension 1024

It spends money: one embedding call per gold query per run, roughly fifty for
a full pass. It writes nothing.
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from observability import traced
from evals.embedding_encoding import (
    binary_rescore_recall,
    book_recall,
    video_recall,
)
from storage.database import connection, resolve_database_url


DEFAULT_OWNER = "9462f7d3-d576-4ba1-b981-1b617d82fe34"
DEFAULT_BOOK = 526  # the owner's copy of the book the gold set was written against


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--book-id", type=int, default=DEFAULT_BOOK)
    parser.add_argument(
        "--dimension",
        type=int,
        default=1024,
        help="Dimensions to embed queries at; must match what is stored",
    )
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--skip-book", action="store_true")
    parser.add_argument(
        "--binary-rescore",
        action="store_true",
        help="Also sweep binary-code search with rescoring, against exact search",
    )
    return parser


@traced("scripts.measure_embedding_encoding.main", flow="evaluation")
def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    arguments = build_parser().parse_args()
    url = arguments.database_url or resolve_database_url()
    label = f"dim={arguments.dimension}"

    with connection(url) as database:
        if not arguments.skip_video:
            print(
                video_recall(
                    database,
                    owner_id=arguments.owner,
                    dimension=arguments.dimension,
                    label=f"video {label}",
                ).line()
            )
        if not arguments.skip_book:
            print(
                book_recall(
                    database,
                    owner_id=arguments.owner,
                    book_id=arguments.book_id,
                    dimension=arguments.dimension,
                    label=f"book  {label}",
                ).line()
            )
        if arguments.binary_rescore:
            print("\nbinary codes + rescoring, recall against exact search:")
            for depth, recall in binary_rescore_recall(
                database, dimension=arguments.dimension
            ).items():
                print(f"  rescore depth {depth:>3}: {recall:.3f}")
    return 0


if __name__ == "__main__":
    load_dotenv()
    sys.exit(main())
