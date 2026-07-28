"""Measure the adjacency rule that selects figures for an answer.

Two modes, because only one of them can run without human labels.

**Near-miss diagnostic (no labels needed).** For every figure in the corpus,
how far is it from the nearest page of its own node's text? A figure sitting
outside the pages a node's evidence covers is invisible to the current rule,
so this distribution is a direct measure of what `PAGE_TOLERANCE = 0` costs
and what widening it would buy.

**Precision and recall (needs labels).** Given a gold file of questions with
the figures a good answer should show, run retrieval, select figures, and
score. The file is optional: hand-labelling requires reading the book, so the
diagnostic is what this project can report today, and the honest statement is
that precision and recall are unmeasured until those labels exist.

    uv run python -m scripts.evaluate_figures
    uv run python -m scripts.evaluate_figures --gold evaluation/figure_gold.json
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from storage.database import connection as database_connection, parse_owner_id
from study.figures import select_figures  # noqa: F401  (used in labelled mode)


def near_miss_distribution(connection, *, owner_id) -> dict:
    """How far each figure sits from the text pages of its own node.

    Distance 0 means the figure shares a page with its node's text, which is
    the only case the current rule can select. Anything further is a figure
    the rule will miss whenever that node is cited.
    """

    rows = connection.execute(
        """
        with figures as (
            select
                content_blocks.id as block_id,
                content_blocks.book_id,
                content_blocks.node_id,
                content_blocks.page_number as figure_page
            from content_blocks
            join image_blocks
              on image_blocks.block_id = content_blocks.id
             and image_blocks.owner_id = content_blocks.owner_id
            where content_blocks.owner_id = %s
              and content_blocks.block_type = 'image'
        ),
        text_pages as (
            select node_id, page_number
            from content_blocks
            where owner_id = %s and block_type = 'text'
        )
        select
            figures.book_id,
            figures.block_id,
            figures.figure_page,
            min(abs(text_pages.page_number - figures.figure_page)) as distance
        from figures
        left join text_pages on text_pages.node_id = figures.node_id
        group by figures.book_id, figures.block_id, figures.figure_page
        """,
        (owner_id, owner_id),
    ).fetchall()

    distances = Counter()
    for row in rows:
        distances[row["distance"] if row["distance"] is not None else "no text"] += 1

    total = len(rows)
    reachable = sum(count for key, count in distances.items() if key == 0)
    return {
        "figures": total,
        "reachable_at_tolerance_0": reachable,
        "reachable_fraction": round(reachable / total, 4) if total else 0.0,
        "distance_histogram": {str(key): count for key, count in sorted(
            distances.items(), key=lambda item: (isinstance(item[0], str), item[0])
        )},
    }


def corpus_summary(connection, *, owner_id) -> list[dict]:
    rows = connection.execute(
        """
        select
            books.id as book_id,
            books.title,
            count(image_blocks.block_id) as figures,
            count(distinct content_blocks.node_id) as nodes_with_figures
        from books
        left join content_blocks
          on content_blocks.book_id = books.id
         and content_blocks.owner_id = books.owner_id
         and content_blocks.block_type = 'image'
        left join image_blocks
          on image_blocks.block_id = content_blocks.id
         and image_blocks.owner_id = content_blocks.owner_id
        where books.owner_id = %s
        group by books.id, books.title
        order by books.id
        """,
        (owner_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def score_against_gold(connection, *, owner_id, gold: dict) -> dict:
    """Precision and recall of adjacency selection over labelled questions."""

    from retrieval.langchain import BookRetriever
    from study.contracts import EvidenceRef

    true_positives = 0
    selected_total = 0
    expected_total = 0
    per_question = []

    for question in gold["questions"]:
        expected = set(question["expected_figure_block_ids"])
        documents = BookRetriever(
            database_url=os.getenv("DATABASE_URL", ""),
            owner_id=str(owner_id),
            mode=question.get("retrieval_mode", "hybrid"),
            book_ids=question.get("book_ids"),
            k=question.get("k", 5),
        ).invoke(question["query"])
        evidence = [
            EvidenceRef(
                node_id=document.metadata.get("node_id", 0),
                pages=list(
                    range(
                        document.metadata["start_page"],
                        document.metadata["end_page"] + 1,
                    )
                ),
                path=document.metadata["path"],
                book_id=document.metadata.get("book_id"),
                rank=rank,
            )
            for rank, document in enumerate(documents, start=1)
        ]
        selected = {
            figure.block_id
            for figure in select_figures(
                connection, owner_id=owner_id, evidence=evidence
            )
        }
        hits = len(selected & expected)
        true_positives += hits
        selected_total += len(selected)
        expected_total += len(expected)
        per_question.append(
            {
                "id": question["id"],
                "selected": sorted(selected),
                "expected": sorted(expected),
                "hits": hits,
            }
        )

    precision = true_positives / selected_total if selected_total else 0.0
    recall = true_positives / expected_total if expected_total else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "selected_total": selected_total,
        "expected_total": expected_total,
        "per_question": per_question,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner-id",
        default=os.getenv("DEFAULT_OWNER_ID"),
        help="Owner whose corpus to measure.",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        help="Optional labelled question set for precision and recall.",
    )
    arguments = parser.parse_args()
    if not arguments.owner_id:
        parser.error("set DEFAULT_OWNER_ID or pass --owner-id")

    owner = parse_owner_id(arguments.owner_id)
    report: dict = {}
    with database_connection(readonly=True) as connection:
        report["corpus"] = corpus_summary(connection, owner_id=owner)
        report["near_miss"] = near_miss_distribution(connection, owner_id=owner)
        if arguments.gold:
            gold = json.loads(arguments.gold.read_text())
            report["labelled"] = score_against_gold(
                connection, owner_id=owner, gold=gold
            )

    print(json.dumps(report, indent=2, default=str))
    if "labelled" not in report:
        print(
            "\nPrecision and recall are unmeasured: no labelled figure set was "
            "supplied. The near-miss distribution above bounds what the current "
            "tolerance can reach at best.",
        )


if __name__ == "__main__":
    main()
