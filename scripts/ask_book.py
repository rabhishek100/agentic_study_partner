"""CLI adapter for the shared routed study query handler."""

import argparse

from study.query import QueryExecutionError, answer_query
from study.scope import ScopeResolutionError
from study.summarize import ContextWindowExceededError


def answer_question(*args, **kwargs) -> str:
    """Backward-compatible alias for callers of the original CLI module."""

    return answer_query(*args, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument(
        "--retrieval-mode",
        choices=("bm25", "vector", "hybrid", "hybrid_rerank"),
        default="hybrid",
    )
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--chroma-path", default="data/chroma")
    args = parser.parse_args()
    try:
        print(
            answer_query(
                args.question,
                chroma_path=args.chroma_path,
                book_id=args.book_id,
                retrieval_mode=args.retrieval_mode,
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
