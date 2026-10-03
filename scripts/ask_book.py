"""CLI adapter for the shared routed study query handler."""


import argparse

from observability import traced
from storage.database import environment_owner_id, parse_owner_id
from study.query import QueryExecutionError, answer_query
from study.scope import ScopeResolutionError
from study.summarize import ContextWindowExceededError


def answer_question(*args, **kwargs) -> str:
    """Backward-compatible alias for callers of the original CLI module."""

    return answer_query(*args, **kwargs)


@traced("scripts.ask_book.main", flow="cli")
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument(
        "--retrieval-mode",
        choices=("bm25", "vector", "hybrid", "hybrid_rerank"),
        default="hybrid",
    )
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--database-url", help="Postgres URL; defaults to DATABASE_URL")
    parser.add_argument(
        "--owner-id",
        help="Owner UUID; defaults to DEFAULT_OWNER_ID",
    )
    args = parser.parse_args()
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )
    try:
        print(
            answer_query(
                args.question,
                database_url=args.database_url,
                book_id=args.book_id,
                retrieval_mode=args.retrieval_mode,
                owner_id=owner_id,
            )
        )
    except (
        ContextWindowExceededError,
        QueryExecutionError,
        ScopeResolutionError,
    ) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
