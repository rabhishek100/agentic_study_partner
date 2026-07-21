"""Build the canonical retrieval comparison report artifact."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from retrieval.reranker import (
    DEFAULT_RERANKER_MODEL,
    DEFAULT_RERANKER_REVISION,
)
from scripts.evaluate_retrieval import RETRIEVAL_MODES, evaluate


TITLE = "Retrieval Evaluation: Four Ranking Strategies"
MODE_LABELS = {
    "bm25": "BM25",
    "vector": "Vector",
    "hybrid": "Hybrid",
    "hybrid_rerank": "Hybrid + reranker",
}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the retrieval comparison report artifact."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/retrieval.sqlite3"),
    )
    parser.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
    )
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=Path("evaluation/retrieval_gold_seed.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/retrieval_comparison_artifact.json"),
    )
    return parser


def _percent(value: float) -> str:
    return f"{value:.1%}"


def _format_pages(pages: list[int]) -> str:
    if not pages:
        return "unknown"
    ranges = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}–{previous}")
    return ", ".join(ranges)


def _source(generated_at: str, model_manifest: dict) -> dict:
    return {
        "id": "retrieval-comparison",
        "label": "Gold-set retrieval comparison",
        "path": "evaluation/retrieval_gold_seed.json",
        "query": {
            "engine": (
                "Python, SQLite FTS5, local Chroma, and a local cross-encoder"
            ),
            "language": "python",
            "description": (
                "Runs the frozen gold questions against BM25, dense vector "
                "search, reciprocal rank fusion, and cross-encoder reranking."
            ),
            "sql": (
                "SELECT id, source_book_id, source_node_id, toc_index, "
                "chunk_index, section_title, path_text, start_page, end_page, "
                "text, content_types_json FROM chunks "
                "WHERE source_book_id = :book_id "
                "ORDER BY toc_index, chunk_index"
            ),
            "executed_at": generated_at,
            "tables_used": [
                "data/retrieval.sqlite3: chunks",
                "data/retrieval.sqlite3: chunks_fts",
                "data/chroma: book_text_chunks",
            ],
            "filters": [
                "Gold-set source SHA-256 must match the indexed book",
                "Top five distinct TOC nodes retained per question",
                "Reranker scores the top 20 RRF chunks before node collapse",
                "Unanswerable probes excluded from recall and MRR",
            ],
            "metric_definitions": [
                "Recall@k is required evidence nodes found in the first k distinct retrieved nodes divided by all required evidence nodes.",
                "MRR@5 is the reciprocal rank of the first required evidence node within five distinct nodes; zero when absent.",
                "Hybrid uses unweighted reciprocal rank fusion with rank constant 60 over the top 20 BM25 and vector chunks.",
                "Candidate Recall@20 measures whether required nodes are present before cross-encoder reranking.",
                "Hybrid + reranker scores the 20 RRF candidates with a pinned local cross-encoder and then keeps five distinct nodes.",
            ],
            "model": model_manifest,
        },
    }


def _result_map(evaluation: dict) -> dict[str, dict[str, dict]]:
    mapped: dict[str, dict[str, dict]] = {}
    for mode, result in evaluation["retrievers"].items():
        for question in [
            *result["answerable_results"],
            *result["unanswerable_probes"],
        ]:
            mapped.setdefault(question["id"], {})[mode] = question
    return mapped


def _question_markdown(mode_results: dict[str, dict]) -> str:
    first = next(iter(mode_results.values()))
    lines = [
        f"## {first['id']} — Retrieval comparison",
        "",
        f"**Question:** {first['query']}",
        "",
    ]
    if first["status"] == "probe":
        lines.extend(
            [
                f"**Expected behavior:** Abstain. {first['answerability_reason']}",
                "",
                "**Plausible near-miss sections:**",
                "",
            ]
        )
        for evidence in first["near_miss_evidence"]:
            lines.append(
                f"- Node {evidence['node_id']}, {evidence['path']} "
                f"(PDF pp. {_format_pages(evidence['pages'])}) — "
                f"{evidence['reason_not_sufficient']}"
            )
        lines.extend(
            [
                "",
                "These rows diagnose what each retriever surfaces; retrieval "
                "alone does not establish answerability.",
            ]
        )
        return "\n".join(lines)

    lines.extend(["**Required evidence:**", ""])
    for evidence in first["expected_evidence"]:
        lines.append(
            f"- Node {evidence['node_id']}, {evidence['path']} "
            f"(PDF pp. {_format_pages(evidence['pages'])}) — "
            f"{evidence['evidence_summary']}"
        )
    lines.extend(["", "**Coverage by method:**", ""])
    for mode in RETRIEVAL_MODES:
        result = mode_results[mode]
        missing = sorted(
            set(result["expected_nodes"]) - set(result["retrieved_nodes_at_5"])
        )
        suffix = f"; missing nodes {missing}" if missing else ""
        candidate_suffix = (
            f"; candidate Recall@20 "
            f"{_percent(result['candidate_recall_at_20'])}"
            if "candidate_recall_at_20" in result
            else ""
        )
        lines.append(
            f"- {MODE_LABELS[mode]}: Recall@3 "
            f"{_percent(result['recall_at_3'])}, Recall@5 "
            f"{_percent(result['recall_at_5'])}{suffix}{candidate_suffix}."
        )
    return "\n".join(lines)


def _retrieval_rows(mode_results: dict[str, dict]) -> list[dict]:
    rows = []
    for mode in RETRIEVAL_MODES:
        result = mode_results[mode]
        for retrieval in result["retrievals"]:
            judgment = retrieval["judgment"]
            if result["status"] == "probe":
                judgment = "near miss" if judgment == "expected" else "other"
            rows.append(
                {
                    "method": MODE_LABELS[mode],
                    "rank": retrieval["rank"],
                    "judgment": judgment,
                    "node_id": retrieval["node_id"],
                    "section": retrieval["section"],
                    "pages": retrieval["pages"],
                    "score": round(retrieval["score"], 4),
                    "excerpt": retrieval["excerpt"],
                }
            )
    return rows


def build_artifact(
    database: Path,
    gold_set: Path,
    *,
    chroma_path: Path,
) -> dict:
    evaluation = evaluate(
        database,
        gold_set,
        chroma_path=chroma_path,
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    manifest_path = chroma_path / "index_manifest.json"
    vector_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"status": "manifest unavailable"}
    )
    model_manifest = {
        "vector_index": vector_manifest,
        "reranker": {
            "model": DEFAULT_RERANKER_MODEL,
            "revision": DEFAULT_RERANKER_REVISION,
            "device": os.getenv(
                "RERANKER_DEVICE",
                os.getenv("EMBEDDING_DEVICE", "cpu"),
            ),
            "candidate_limit": 20,
        },
    }
    source = _source(generated_at, model_manifest)
    by_question = _result_map(evaluation)

    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    try:
        chunk_count, node_count = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT source_node_id) FROM chunks"
        ).fetchone()
    finally:
        connection.close()

    comparisons = {
        row["mode"]: row for row in evaluation["comparison"]
    }
    best_mode = max(
        RETRIEVAL_MODES,
        key=lambda mode: (
            comparisons[mode]["mean_recall_at_5"],
            comparisons[mode]["mrr_at_5"],
        ),
    )
    bm25_recall = comparisons["bm25"]["mean_recall_at_5"]
    vector_recall = comparisons["vector"]["mean_recall_at_5"]
    hybrid_recall = comparisons["hybrid"]["mean_recall_at_5"]
    rerank_recall = comparisons["hybrid_rerank"]["mean_recall_at_5"]
    hybrid_delta = hybrid_recall - bm25_recall
    vector_delta = vector_recall - bm25_recall
    hybrid_mrr_delta = (
        comparisons["hybrid"]["mrr_at_5"] - comparisons["bm25"]["mrr_at_5"]
    )
    rerank_delta = rerank_recall - hybrid_recall
    rerank_mrr_delta = (
        comparisons["hybrid_rerank"]["mrr_at_5"]
        - comparisons["hybrid"]["mrr_at_5"]
    )
    candidate_recall = comparisons["hybrid_rerank"][
        "mean_candidate_recall_at_20"
    ]
    hybrid_categories = evaluation["retrievers"]["hybrid"]["metrics_by_category"]
    vector_categories = evaluation["retrievers"]["vector"]["metrics_by_category"]
    rerank_categories = evaluation["retrievers"]["hybrid_rerank"][
        "metrics_by_category"
    ]

    summary_row = {
        f"{mode}_{metric}": comparisons[mode][field]
        for mode in RETRIEVAL_MODES
        for metric, field in (
            ("recall_at_5", "mean_recall_at_5"),
            ("mrr_at_5", "mrr_at_5"),
        )
    }
    summary_row["hybrid_rerank_candidate_recall_at_20"] = candidate_recall
    datasets = {
        "summary_metrics": [summary_row],
        "method_metrics": [
            {
                "method": MODE_LABELS[mode],
                "metric": metric_label,
                "value": comparisons[mode][field],
                "question_count": evaluation["answerable_question_count"],
            }
            for mode in RETRIEVAL_MODES
            for metric_label, field in (
                ("Recall@3", "mean_recall_at_3"),
                ("Recall@5", "mean_recall_at_5"),
                ("MRR@5", "mrr_at_5"),
            )
        ],
        "category_recall": [
            {
                "category": category.replace("_", " ").title(),
                "method": MODE_LABELS[mode],
                "recall_at_5": metrics["mean_recall_at_5"],
                "question_count": metrics["question_count"],
            }
            for mode in RETRIEVAL_MODES
            for category, metrics in evaluation["retrievers"][mode][
                "metrics_by_category"
            ].items()
        ],
    }

    cards = [
        {
            "id": f"{mode}-quality",
            "dataset": "summary_metrics",
            "description": (
                f"{MODE_LABELS[mode]} required-node coverage and first-hit rank."
            ),
            "sourceId": "retrieval-comparison",
            "metrics": [
                {
                    "label": f"{MODE_LABELS[mode]} Recall@5",
                    "field": f"{mode}_recall_at_5",
                    "format": "percent",
                },
                {
                    "label": "MRR@5",
                    "field": f"{mode}_mrr_at_5",
                    "format": "percent",
                },
            ],
        }
        for mode in RETRIEVAL_MODES
    ]
    cards.append(
        {
            "id": "reranker-shortlist-quality",
            "dataset": "summary_metrics",
            "description": (
                "Required evidence available to the cross-encoder before "
                "reranking."
            ),
            "sourceId": "retrieval-comparison",
            "metrics": [
                {
                    "label": "Reranker candidate Recall@20",
                    "field": "hybrid_rerank_candidate_recall_at_20",
                    "format": "percent",
                }
            ],
        }
    )
    charts = [
        {
            "id": "method-quality-chart",
            "title": "Retrieval quality by method",
            "subtitle": (
                f"Mean node-level scores across "
                f"{evaluation['answerable_question_count']} answerable "
                "gold questions."
            ),
            "type": "bar",
            "intent": "comparison",
            "dataset": "method_metrics",
            "sourceId": "retrieval-comparison",
            "encodings": {
                "x": {
                    "field": "method",
                    "type": "nominal",
                    "label": "Retrieval method",
                },
                "y": {
                    "field": "value",
                    "type": "quantitative",
                    "format": "percent",
                    "label": "Mean score",
                },
                "color": {
                    "field": "metric",
                    "type": "nominal",
                    "label": "Metric",
                },
            },
            "settings": {
                "groupMode": "grouped",
                "orientation": "vertical",
                "showValues": True,
            },
            "legend": {"position": "bottom", "title": "Metric"},
            "palette": {"kind": "categorical"},
            "layout": "full",
        },
        {
            "id": "category-recall-chart",
            "title": "Recall@5 by question category",
            "subtitle": (
                "Required-node coverage for exact-term, paraphrase, and "
                "multi-section questions."
            ),
            "type": "bar",
            "intent": "comparison",
            "dataset": "category_recall",
            "sourceId": "retrieval-comparison",
            "encodings": {
                "x": {
                    "field": "category",
                    "type": "nominal",
                    "label": "Question category",
                },
                "y": {
                    "field": "recall_at_5",
                    "type": "quantitative",
                    "format": "percent",
                    "label": "Mean Recall@5",
                },
                "color": {
                    "field": "method",
                    "type": "nominal",
                    "label": "Retrieval method",
                },
                "tooltip": [
                    {"field": "question_count", "type": "quantitative"},
                    {
                        "field": "recall_at_5",
                        "type": "quantitative",
                        "format": "percent",
                    },
                ],
            },
            "settings": {
                "groupMode": "grouped",
                "orientation": "vertical",
                "showValues": True,
            },
            "legend": {"position": "bottom", "title": "Method"},
            "palette": {"kind": "categorical"},
            "layout": "full",
        },
    ]
    tables = []
    blocks = [
        {
            "id": "title",
            "type": "markdown",
            "body": f"# {TITLE}",
            "layout": "full",
        },
        {
            "id": "technical-summary",
            "type": "markdown",
            "body": (
                "## Technical summary\n\n"
                f"**{MODE_LABELS[best_mode]} has the strongest aggregate "
                "retrieval result on this seed set.** Its Recall@5 is "
                f"{_percent(comparisons[best_mode]['mean_recall_at_5'])} and "
                f"MRR@5 is {_percent(comparisons[best_mode]['mrr_at_5'])}. "
                f"Hybrid changes Recall@5 by {hybrid_delta:+.1%} versus the "
                f"frozen BM25 baseline, while changing MRR@5 by "
                f"{hybrid_mrr_delta:+.1%}. Vector alone changes Recall@5 by "
                f"{vector_delta:+.1%}. Cross-encoder reranking changes "
                f"Recall@5 by {rerank_delta:+.1%} and MRR@5 by "
                f"{rerank_mrr_delta:+.1%} versus hybrid. The 20-candidate "
                f"shortlist has {_percent(candidate_recall)} required-node "
                "recall, separating candidate-generation misses from ranking "
                "errors. Because reranking does not improve Recall@5 on this "
                "set and adds a model inference stage, hybrid remains the "
                "interactive default until traced latency justifies changing "
                "it."
            ),
            "sourceId": "retrieval-comparison",
            "layout": "full",
        },
        {
            "id": "headline-metrics",
            "type": "metric-strip",
            "cardIds": [
                *[f"{mode}-quality" for mode in RETRIEVAL_MODES],
                "reranker-shortlist-quality",
            ],
            "layout": "full",
        },
        {
            "id": "aggregate-finding",
            "type": "markdown",
            "body": (
                "## Reranking is measured against the fused shortlist\n\n"
                f"Hybrid raises Recall@5 from {_percent(bm25_recall)} to "
                f"{_percent(hybrid_recall)}, but its MRR@5 is "
                f"{_percent(comparisons['hybrid']['mrr_at_5'])} versus "
                f"{_percent(comparisons['bm25']['mrr_at_5'])} for BM25. "
                f"Reranking changes the fused result to "
                f"{_percent(rerank_recall)} Recall@5 and "
                f"{_percent(comparisons['hybrid_rerank']['mrr_at_5'])} "
                "MRR@5. Candidate Recall@20 is "
                f"{_percent(candidate_recall)}; any remaining expected node "
                "outside that shortlist cannot be recovered by the reranker."
            ),
            "layout": "full",
        },
        {
            "id": "method-quality",
            "type": "chart",
            "chartId": "method-quality-chart",
            "layout": "full",
        },
        {
            "id": "category-finding",
            "type": "markdown",
            "body": (
                "## Category results locate ranking gains and regressions\n\n"
                "Hybrid reaches 100% Recall@5 for exact-term and paraphrase "
                f"questions, while multi-section Recall@5 remains "
                f"{_percent(hybrid_categories['multi_section']['mean_recall_at_5'])}. "
                "Vector-only paraphrase Recall@5 is "
                f"{_percent(vector_categories['paraphrase']['mean_recall_at_5'])}, "
                "while reranked paraphrase Recall@5 is "
                f"{_percent(rerank_categories['paraphrase']['mean_recall_at_5'])}. "
                "These category cuts are diagnostic because each contains "
                "only three to five scored questions."
            ),
            "layout": "full",
        },
        {
            "id": "category-recall",
            "type": "chart",
            "chartId": "category-recall-chart",
            "layout": "full",
        },
        {
            "id": "scope-definitions",
            "type": "markdown",
            "body": (
                "## Scope and metric definitions\n\n"
                f"The evaluated corpus contains {chunk_count} citation-aware "
                f"chunks from {node_count} TOC nodes in one technical book. "
                f"{evaluation['answerable_question_count']} answerable "
                "questions contribute to Recall@3, Recall@5, and MRR@5; three "
                "unanswerable questions are diagnostic probes. "
                "All methods are evaluated against the same node-level evidence "
                "judgments and the same derived chunks."
            ),
            "sourceId": "retrieval-comparison",
            "layout": "full",
        },
        {
            "id": "methodology",
            "type": "markdown",
            "body": (
                "## Methods use one corpus but different ranking signals\n\n"
                "BM25 uses weighted SQLite FTS5 fields: section title 5×, "
                "hierarchy path 2×, and body 1×. Vector search embeds book and "
                "hierarchy context plus the complete chunk body, then searches "
                "a cosine HNSW index in local Chroma. Hybrid applies unweighted "
                "reciprocal rank fusion with constant 60 to the top 20 chunks "
                "from each method. The reranked mode applies the pinned "
                f"`{DEFAULT_RERANKER_MODEL}` cross-encoder to the first 20 RRF "
                "chunks. Each query is paired with the chunk hierarchy and "
                "complete body; inputs are checked against the model's "
                "8,192-token limit instead of being silently truncated. The "
                "final ranked lists are collapsed to distinct TOC nodes before "
                "evaluation."
            ),
            "sourceId": "retrieval-comparison",
            "layout": "full",
        },
        {
            "id": "question-audit-intro",
            "type": "markdown",
            "body": (
                "## Question-by-question retrieval audit\n\n"
                "Each section lists the expected evidence and the five "
                "highest-ranked distinct nodes from all four methods. Raw "
                "BM25, cosine-similarity, RRF, and cross-encoder scores are "
                "method-specific and must not be compared across methods."
            ),
            "layout": "full",
        },
    ]

    for question_id, mode_results in by_question.items():
        dataset_id = f"{question_id}-retrievals"
        table_id = f"{question_id}-table"
        datasets[dataset_id] = _retrieval_rows(mode_results)
        tables.append(
            {
                "id": table_id,
                "title": f"{question_id} ranked retrievals",
                "subtitle": (
                    "Five distinct source nodes per method with citations and "
                    "gold-set judgments."
                ),
                "dataset": dataset_id,
                "defaultSort": {"field": "rank", "direction": "asc"},
                "density": "dense",
                "sourceId": "retrieval-comparison",
                "layout": "full",
                "columns": [
                    {"field": "method", "label": "Method", "type": "text"},
                    {"field": "rank", "label": "Rank", "type": "number"},
                    {"field": "judgment", "label": "Judgment", "type": "text"},
                    {"field": "node_id", "label": "Node", "type": "number"},
                    {"field": "section", "label": "Section", "type": "text"},
                    {"field": "pages", "label": "PDF pages", "type": "text"},
                    {"field": "score", "label": "Method score", "type": "number"},
                    {"field": "excerpt", "label": "Chunk excerpt", "type": "text"},
                ],
            }
        )
        blocks.extend(
            [
                {
                    "id": f"{question_id}-summary",
                    "type": "markdown",
                    "body": _question_markdown(mode_results),
                    "layout": "full",
                },
                {
                    "id": f"{question_id}-results",
                    "type": "table",
                    "tableId": table_id,
                    "layout": "full",
                },
            ]
        )

    blocks.extend(
        [
            {
                "id": "limitations",
                "type": "markdown",
                "body": (
                    "## Limits keep this result diagnostic\n\n"
                    "The seed set is small, node-level, and authored alongside "
                    "the relevance judgments, so it is not a population "
                    "estimate. HNSW is approximate, though index scale is tiny. "
                    "Unanswerable behavior and citation correctness still need "
                    "answer-level evaluation. Review the pooled union of all "
                    "four methods for missing relevance judgments before "
                    "treating small differences as conclusive."
                ),
                "layout": "full",
            },
            {
                "id": "next-steps",
                "type": "markdown",
                "body": (
                    "## Next steps follow the measured failures\n\n"
                    "1. Review newly surfaced vector- and reranker-only nodes "
                    "and update the gold set only when they are genuinely "
                    "relevant.\n"
                    "2. Keep hybrid as the interactive default while using "
                    "the reranked mode as an explicit quality experiment; "
                    "compare traced latency before promoting it.\n"
                    "3. Add answer-level citation and abstention evaluation.\n"
                    "4. Add decomposition or hierarchy expansion only for "
                    "remaining multi-section misses."
                ),
                "layout": "full",
            },
            {
                "id": "further-questions",
                "type": "markdown",
                "body": (
                    "## Further questions\n\n"
                    "- Do pooled judgments change the apparent winner?\n"
                    "- Does reranking improve multi-section coverage without "
                    "displacing exact-term matches?\n"
                    "- Which remaining misses require query decomposition "
                    "rather than another retrieval index?"
                ),
                "layout": "full",
            },
        ]
    )

    return {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": TITLE,
            "description": (
                "Gold-set comparison of lexical, semantic, fused, and "
                "cross-encoder-reranked retrieval."
            ),
            "generatedAt": generated_at,
            "cards": cards,
            "charts": charts,
            "tables": tables,
            "sources": [source],
            "blocks": blocks,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": datasets,
        },
        "sources": [source],
    }


def main() -> None:
    args = build_argument_parser().parse_args()
    artifact = build_artifact(
        args.database,
        args.gold_set,
        chroma_path=args.chroma_path,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote canonical report artifact to {args.output}")


if __name__ == "__main__":
    main()
