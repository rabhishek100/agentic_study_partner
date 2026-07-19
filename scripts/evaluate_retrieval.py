"""Compare BM25, vector, and hybrid retrieval against the gold set."""

import argparse
import json
from pathlib import Path
import sqlite3

from retrieval.search import RetrievalMode, retrieve
from retrieval.vector import LocalEmbedder, persistent_client


RETRIEVAL_MODES: tuple[RetrievalMode, ...] = ("bm25", "vector", "hybrid")


def result_rows(results: list, expected_nodes: set[int]) -> list[dict]:
    """Convert search results into compact, reader-facing audit rows."""

    rows = []
    for rank, result in enumerate(results, start=1):
        excerpt = " ".join(result.text.split())
        rows.append(
            {
                "rank": rank,
                "chunk_id": result.chunk_id,
                "node_id": result.source_node_id,
                "chunk_index": result.chunk_index,
                "section": result.section_title,
                "section_path": result.path_text,
                "pages": (
                    str(result.start_page)
                    if result.start_page == result.end_page
                    else f"{result.start_page}–{result.end_page}"
                ),
                "score": result.score,
                "retrieval_method": result.retrieval_method,
                "judgment": (
                    "expected"
                    if result.source_node_id in expected_nodes
                    else "extra"
                ),
                "excerpt": excerpt[:200] + ("…" if len(excerpt) > 200 else ""),
            }
        )
    return rows


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare retrieval methods against node-level judgments."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/retrieval.sqlite3"),
        help="Derived retrieval database (default: data/retrieval.sqlite3)",
    )
    parser.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="Local Chroma directory (default: data/chroma)",
    )
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=Path("evaluation/retrieval_gold_seed.json"),
        help="Retrieval gold set JSON",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=RETRIEVAL_MODES,
        default=list(RETRIEVAL_MODES),
    )
    return parser


def _metrics(results: list[dict]) -> dict:
    def mean(field: str) -> float:
        return sum(result[field] for result in results) / len(results)

    return {
        "mean_recall_at_3": mean("recall_at_3"),
        "mean_recall_at_5": mean("recall_at_5"),
        "mrr_at_5": mean("reciprocal_rank_at_5"),
    }


def _category_metrics(results: list[dict]) -> dict:
    metrics = {}
    for category in sorted({result["category"] for result in results}):
        category_results = [
            result for result in results if result["category"] == category
        ]
        values = _metrics(category_results)
        metrics[category] = {
            "question_count": len(category_results),
            **values,
        }
    return metrics


def _evaluate_mode(
    connection: sqlite3.Connection,
    gold: dict,
    *,
    mode: RetrievalMode,
    book_id: int,
    chroma_path: Path,
    client,
    embedder,
) -> dict:
    answerable_results = []
    for question in gold["questions"]:
        if not question["answerable"]:
            continue
        expected = {
            evidence["node_id"] for evidence in question["expected_evidence"]
        }
        ranked = retrieve(
            connection,
            question["query"],
            mode=mode,
            book_id=book_id,
            limit=5,
            unique_nodes=True,
            chroma_path=chroma_path,
            client=client,
            embedder=embedder,
        )
        retrieved = [result.source_node_id for result in ranked]
        recall_at_5 = len(expected.intersection(retrieved)) / len(expected)
        first_relevant_rank = next(
            (
                rank
                for rank, node_id in enumerate(retrieved, start=1)
                if node_id in expected
            ),
            None,
        )
        answerable_results.append(
            {
                "id": question["id"],
                "query": question["query"],
                "category": question["category"],
                "status": (
                    "full"
                    if recall_at_5 == 1
                    else "partial"
                    if recall_at_5 > 0
                    else "miss"
                ),
                "expected_evidence": question["expected_evidence"],
                "expected_nodes": sorted(expected),
                "retrieved_nodes_at_5": retrieved,
                "retrievals": result_rows(ranked, expected),
                "recall_at_3": len(expected.intersection(retrieved[:3]))
                / len(expected),
                "recall_at_5": recall_at_5,
                "reciprocal_rank_at_5": (
                    1 / first_relevant_rank if first_relevant_rank else 0
                ),
            }
        )

    unanswerable_results = []
    for question in gold["questions"]:
        if question["answerable"]:
            continue
        ranked = retrieve(
            connection,
            question["query"],
            mode=mode,
            book_id=book_id,
            limit=5,
            unique_nodes=True,
            chroma_path=chroma_path,
            client=client,
            embedder=embedder,
        )
        unanswerable_results.append(
            {
                "id": question["id"],
                "query": question["query"],
                "category": question["category"],
                "status": "probe",
                "answerability_reason": question["answerability_reason"],
                "near_miss_evidence": question["near_miss_evidence"],
                "top_nodes": [result.source_node_id for result in ranked],
                "retrievals": result_rows(
                    ranked,
                    {
                        evidence["node_id"]
                        for evidence in question["near_miss_evidence"]
                    },
                ),
                "note": (
                    "Retrieval only; abstention is evaluated after answer "
                    "generation."
                ),
            }
        )

    return {
        "mode": mode,
        "answerable_question_count": len(answerable_results),
        "metrics": _metrics(answerable_results),
        "metrics_by_category": _category_metrics(answerable_results),
        "answerable_results": answerable_results,
        "unanswerable_probes": unanswerable_results,
    }


def evaluate(
    database: Path,
    gold_set: Path,
    *,
    chroma_path: Path = Path("data/chroma"),
    modes: tuple[RetrievalMode, ...] = RETRIEVAL_MODES,
) -> dict:
    gold = json.loads(gold_set.read_text(encoding="utf-8"))
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        build = connection.execute(
            """
            SELECT source_book_id, source_file_hash
            FROM chunk_builds
            WHERE source_file_hash = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (gold["book"]["source_file_sha256"],),
        ).fetchone()
        if build is None:
            raise ValueError("retrieval database has no build matching the gold set")

        uses_vectors = any(mode in ("vector", "hybrid") for mode in modes)
        client = persistent_client(chroma_path) if uses_vectors else None
        embedder = LocalEmbedder() if uses_vectors else None
        retrievers = {
            mode: _evaluate_mode(
                connection,
                gold,
                mode=mode,
                book_id=build["source_book_id"],
                chroma_path=chroma_path,
                client=client,
                embedder=embedder,
            )
            for mode in modes
        }
        return {
            "set_id": gold["set_id"],
            "source_file_sha256": build["source_file_hash"],
            "answerable_question_count": sum(
                question["answerable"] for question in gold["questions"]
            ),
            "retrievers": retrievers,
            "comparison": [
                {
                    "mode": mode,
                    **retrievers[mode]["metrics"],
                }
                for mode in modes
            ],
        }
    finally:
        connection.close()


def main() -> None:
    args = build_argument_parser().parse_args()
    result = evaluate(
        args.database,
        args.gold_set,
        chroma_path=args.chroma_path,
        modes=tuple(args.modes),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
