"""Build the canonical artifact input for the detailed BM25 HTML report."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from scripts.evaluate_retrieval import evaluate


TITLE = "BM25 Retrieval Evaluation"
RETRIEVAL_SQL = """
SELECT
    chunks.*,
    bm25(chunks_fts, 5.0, 2.0, 1.0) AS score
FROM chunks_fts
JOIN chunks ON chunks.rowid = chunks_fts.rowid
WHERE chunks_fts MATCH :query
  AND chunks.source_book_id = :book_id
ORDER BY score, chunks.toc_index, chunks.chunk_index
LIMIT :candidate_limit
""".strip()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the canonical artifact JSON for the BM25 HTML report."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/retrieval.sqlite3"),
    )
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=Path("evaluation/retrieval_gold_seed.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/bm25_report_artifact.json"),
    )
    return parser


def _percent(value: float) -> str:
    return f"{value:.1%}"


def _format_pages(pages: list[int]) -> str:
    """Format sorted page numbers as compact ranges."""

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


def _source(generated_at: str) -> dict:
    return {
        "id": "bm25-evaluation",
        "label": "BM25 evaluation over citation-aware chunks",
        "path": "data/retrieval.sqlite3",
        "query": {
            "engine": "SQLite FTS5",
            "language": "sql",
            "sql": RETRIEVAL_SQL,
            "description": (
                "Ranks citation-aware chunks with FTS5 BM25 and collapses "
                "results to the first occurrence of each source node."
            ),
            "executed_at": generated_at,
            "tables_used": ["chunks_fts", "chunks", "chunk_sources"],
            "filters": [
                "One indexed book identified by source SHA-256",
                "Navigation-only nodes excluded during chunk construction",
                "Top five distinct source nodes retained per question",
            ],
            "metric_definitions": [
                "Recall@k: required evidence nodes found in the first k distinct retrieved nodes divided by all required evidence nodes.",
                "MRR@5: reciprocal rank of the first required evidence node within the first five distinct retrieved nodes; zero if absent.",
                "Unanswerable questions are retrieval probes and are excluded from recall and MRR.",
            ],
        },
    }


def _question_markdown(result: dict) -> str:
    status_labels = {
        "full": "Full coverage",
        "partial": "Partial coverage",
        "miss": "Miss",
        "probe": "Unanswerable probe",
    }
    lines = [
        f"## {result['id']} — {status_labels[result['status']]}",
        "",
        f"**Question:** {result['query']}",
        "",
    ]
    if result["status"] == "probe":
        lines.extend(
            [
                f"**Expected behavior:** Abstain. {result['answerability_reason']}",
                "",
                "**Plausible near-miss sections:**",
                "",
            ]
        )
        for evidence in result["near_miss_evidence"]:
            pages = _format_pages(evidence["pages"])
            lines.append(
                f"- Node {evidence['node_id']}, {evidence['path']} "
                f"(PDF pp. {pages}) — {evidence['reason_not_sufficient']}"
            )
        lines.extend(
            [
                "",
                "The ranked rows below are diagnostic only; retrieval alone "
                "cannot establish that the question is answerable.",
            ]
        )
        return "\n".join(lines)

    lines.extend(["**Required evidence:**", ""])
    for evidence in result["expected_evidence"]:
        pages = _format_pages(evidence["pages"])
        lines.append(
            f"- Node {evidence['node_id']}, {evidence['path']} "
            f"(PDF pp. {pages}) — {evidence['evidence_summary']}"
        )
    found = len(
        set(result["expected_nodes"]).intersection(result["retrieved_nodes_at_5"])
    )
    lines.extend(
        [
            "",
            f"**Top-five coverage:** {found}/{len(result['expected_nodes'])} "
            f"required nodes; Recall@3 {_percent(result['recall_at_3'])}; "
            f"Recall@5 {_percent(result['recall_at_5'])}.",
        ]
    )
    missing = sorted(
        set(result["expected_nodes"]) - set(result["retrieved_nodes_at_5"])
    )
    if missing:
        lines.append(f"**Missing expected nodes:** {', '.join(map(str, missing))}.")
    return "\n".join(lines)


def _retrieval_rows(result: dict) -> list[dict]:
    rows = []
    for retrieval in result["retrievals"]:
        judgment = retrieval["judgment"]
        if result["status"] == "probe":
            judgment = "near miss" if judgment == "expected" else "other"
        rows.append(
            {
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


def build_artifact(database: Path, gold_set: Path) -> dict:
    evaluation = evaluate(database, gold_set)
    generated_at = datetime.now(timezone.utc).isoformat()
    all_results = [
        *evaluation["answerable_results"],
        *evaluation["unanswerable_probes"],
    ]
    full_count = sum(
        result["status"] == "full" for result in evaluation["answerable_results"]
    )
    partial_count = sum(
        result["status"] == "partial" for result in evaluation["answerable_results"]
    )

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

    datasets = {
        "summary_metrics": [
            {
                "recall_at_3": evaluation["metrics"]["mean_recall_at_3"],
                "recall_at_5": evaluation["metrics"]["mean_recall_at_5"],
                "mrr_at_5": evaluation["metrics"]["mrr_at_5"],
            }
        ],
        "category_recall": [
            {
                "category": category.replace("_", " ").title(),
                "metric": metric_label,
                "recall": metrics[metric_field],
                "question_count": metrics["question_count"],
            }
            for category, metrics in evaluation["metrics_by_category"].items()
            for metric_label, metric_field in (
                ("Recall@3", "mean_recall_at_3"),
                ("Recall@5", "mean_recall_at_5"),
            )
        ],
    }
    cards = [
        {
            "id": "recall-3",
            "dataset": "summary_metrics",
            "description": "Mean required-node coverage within three distinct nodes.",
            "sourceId": "bm25-evaluation",
            "metrics": [
                {
                    "label": "Recall@3",
                    "field": "recall_at_3",
                    "format": "percent",
                }
            ],
        },
        {
            "id": "recall-5",
            "dataset": "summary_metrics",
            "description": "Mean required-node coverage within five distinct nodes.",
            "sourceId": "bm25-evaluation",
            "metrics": [
                {
                    "label": "Recall@5",
                    "field": "recall_at_5",
                    "format": "percent",
                }
            ],
        },
        {
            "id": "mrr-5",
            "dataset": "summary_metrics",
            "description": "Mean reciprocal rank of the first required node.",
            "sourceId": "bm25-evaluation",
            "metrics": [
                {
                    "label": "MRR@5",
                    "field": "mrr_at_5",
                    "format": "percent",
                }
            ],
        },
    ]
    charts = [
        {
            "id": "category-recall-chart",
            "title": "Recall by question category",
            "subtitle": (
                "Multi-section questions have lower evidence coverage than "
                "exact-term and paraphrased questions."
            ),
            "type": "bar",
            "intent": "comparison",
            "dataset": "category_recall",
            "sourceId": "bm25-evaluation",
            "encodings": {
                "x": {
                    "field": "category",
                    "type": "nominal",
                    "label": "Question category",
                },
                "y": {
                    "field": "recall",
                    "type": "quantitative",
                    "format": "percent",
                    "label": "Mean recall",
                },
                "color": {
                    "field": "metric",
                    "type": "nominal",
                    "label": "Cutoff",
                },
                "tooltip": [
                    {"field": "question_count", "type": "quantitative"},
                    {"field": "recall", "type": "quantitative", "format": "percent"},
                ],
            },
            "settings": {
                "groupMode": "grouped",
                "orientation": "vertical",
                "showValues": True,
            },
            "legend": {"position": "bottom", "title": "Cutoff"},
            "palette": {"kind": "categorical"},
            "layout": "full",
        }
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
                f"BM25 fully retrieves all required evidence for {full_count} of "
                f"12 answerable questions and partially covers {partial_count}. "
                f"Overall Recall@5 is {_percent(evaluation['metrics']['mean_recall_at_5'])} "
                f"and MRR@5 is {_percent(evaluation['metrics']['mrr_at_5'])}. "
                "The main measured weakness is multi-section coverage, not "
                "paraphrased single-concept retrieval."
            ),
            "layout": "full",
        },
        {
            "id": "headline-metrics",
            "type": "metric-strip",
            "cardIds": ["recall-3", "recall-5", "mrr-5"],
            "layout": "full",
        },
        {
            "id": "key-finding",
            "type": "markdown",
            "body": (
                "## Multi-section questions account for most missing evidence\n\n"
                "Exact-term Recall@5 is 90.0% and paraphrase Recall@5 is "
                "100.0%, while multi-section Recall@5 is 72.2%. The chart "
                "shows the same comparison at both retrieval cutoffs. This "
                "supports adding decomposition or hierarchy expansion before "
                "considering a vector index."
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
                f"chunks from {node_count} content nodes for one book. Twelve "
                "answerable questions are scored; three deliberately "
                "unanswerable questions are shown only as diagnostic probes. "
                "Retrieval is collapsed to distinct TOC nodes before computing "
                "Recall@3, Recall@5, and MRR@5. PDF page numbers are physical "
                "source pages stored in canonical SQLite."
            ),
            "layout": "full",
        },
        {
            "id": "methodology",
            "type": "markdown",
            "body": (
                "## Methodology\n\n"
                "Each natural-language question is converted to an OR query "
                "over unique normalized terms. FTS5 ranks chunks with weighted "
                "BM25 fields: section title 5×, hierarchy path 2×, and chunk "
                "body 1×. Results are ordered by score, collapsed to the first "
                "chunk for each node, and compared with the frozen node-level "
                "judgments in the seed gold set."
            ),
            "layout": "full",
        },
        {
            "id": "question-audit-intro",
            "type": "markdown",
            "body": (
                "## Question-by-question retrieval audit\n\n"
                "Each section lists the expected evidence followed by the five "
                "highest-ranked distinct nodes. “Expected” marks a gold node; "
                "“extra” is a retrieved node not required by the current "
                "judgment. BM25 scores are negative in SQLite, so more negative "
                "values rank higher."
            ),
            "layout": "full",
        },
    ]

    for result in all_results:
        dataset_id = f"{result['id']}-retrievals"
        table_id = f"{result['id']}-table"
        datasets[dataset_id] = _retrieval_rows(result)
        tables.append(
            {
                "id": table_id,
                "title": f"{result['id']} ranked retrievals",
                "subtitle": "Top five distinct source nodes with citation metadata.",
                "dataset": dataset_id,
                "defaultSort": {"field": "rank", "direction": "asc"},
                "density": "dense",
                "sourceId": "bm25-evaluation",
                "layout": "full",
                "columns": [
                    {"field": "rank", "label": "Rank", "type": "number"},
                    {"field": "judgment", "label": "Judgment", "type": "text"},
                    {"field": "node_id", "label": "Node", "type": "number"},
                    {"field": "section", "label": "Section", "type": "text"},
                    {"field": "pages", "label": "PDF pages", "type": "text"},
                    {"field": "score", "label": "BM25 score", "type": "number"},
                    {"field": "excerpt", "label": "Chunk excerpt", "type": "text"},
                ],
            }
        )
        blocks.extend(
            [
                {
                    "id": f"{result['id']}-summary",
                    "type": "markdown",
                    "body": _question_markdown(result),
                    "layout": "full",
                },
                {
                    "id": f"{result['id']}-results",
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
                    "## Limitations and robustness notes\n\n"
                    "The seed set is intentionally small and node-level, so "
                    "these values are diagnostic rather than population "
                    "estimates. The same author created the questions and "
                    "judgments. Unanswerable handling cannot be scored until a "
                    "sufficiency or answer-generation stage exists. Pooling "
                    "results from a materially different retriever may reveal "
                    "additional relevant nodes that should be adjudicated."
                ),
                "layout": "full",
            },
            {
                "id": "next-steps",
                "type": "markdown",
                "body": (
                    "## Recommended next steps\n\n"
                    "1. Add a minimal grounded-answer path over the retrieved "
                    "chunks.\n"
                    "2. Measure citation correctness and unsupported claims.\n"
                    "3. Add query decomposition or hierarchy expansion for the "
                    "three partially covered multi-node questions.\n"
                    "4. Re-run this unchanged report before considering dense "
                    "or hybrid retrieval."
                ),
                "layout": "full",
            },
            {
                "id": "further-questions",
                "type": "markdown",
                "body": (
                    "## Further questions\n\n"
                    "- Does parent or sibling expansion recover the missing "
                    "required nodes without reducing precision?\n"
                    "- Do chunk-level judgments change the apparent advantage "
                    "of BM25 on paraphrased questions?\n"
                    "- Can a deterministic sufficiency rule reject the three "
                    "unanswerable probes before introducing an LLM judge?"
                ),
                "layout": "full",
            },
        ]
    )

    source = _source(generated_at)
    return {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": TITLE,
            "description": (
                "Detailed expected-versus-actual audit of the seed BM25 retrieval run."
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
    artifact = build_artifact(args.database, args.gold_set)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote canonical report artifact to {args.output}")


if __name__ == "__main__":
    main()
