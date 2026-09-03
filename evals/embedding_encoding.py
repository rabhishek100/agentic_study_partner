"""What a cheaper vector encoding costs, measured on the frozen gold sets.

Embeddings dominate this database, and there are three ways to make them
smaller: fewer bits per component, fewer components, or a compressed search
index with a rescoring pass. Each trades recall for space, and the only
honest way to pick is to ask the gold sets rather than a benchmark table.

This is the runner that answers that. It measures through the real retrievers
— `video.retrieval.retrieve_video_evidence` and `retrieval.search.retrieve` —
so what comes back is the system's recall and not a vector-space proxy. That
distinction turned out to matter: truncating to 1024 dimensions loses about
9% of raw nearest-neighbour agreement, and 1.6% of what the video gold set
actually asks for, because lexical retrieval carries most of the difference.

Findings on 2026-09-03, against float32/3072 as the baseline:

    encoding              video anchors     book nodes
    halfvec, 3072         0.912  (=)        0.931  (=)
    halfvec, 1024         0.897  (-1.6%)    0.889  (-4.5%)

which is why video text runs at 1024 and books stayed at 3072: the same
truncation costs nearly three times more on books, and books are the corpus
that does not grow.

`binary_rescore_recall` measures the third option, which is not deployed:
binary codes for the search and the full vectors only for the top candidates.
At depth 100 it recovered 98.2% of exact search from an index 15 times
smaller — the reserve lever if the corpus ever outgrows the machine.

Caveats that belong with any number this produces. The video set is 34
evidence turns over one lecture and the book set is 12 queries over one book,
so a point of recall is one or two turns moving. It measures retrieval, not
whether the answer written from it stays grounded — that needs the full
runner and its judge. And it spends money: one embedding call per query per
configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import statistics
from typing import Any
from uuid import UUID

from psycopg import Connection

from evals.video import anchor_recall
from retrieval.search import retrieve as retrieve_book
from retrieval.vector import OpenRouterEmbedder
from video.embeddings import OpenRouterTextEmbedder
from video.retrieval import retrieve_video_evidence


EVALUATION = Path(__file__).resolve().parent.parent / "evaluation"
VIDEO_GOLD = EVALUATION / "video_gold.json"
BOOK_GOLD = EVALUATION / "retrieval_gold_seed.json"


@dataclass(frozen=True)
class RecallReport:
    label: str
    queries: int
    mean_recall: float
    fully_recalled: int

    def line(self) -> str:
        return (
            f"{self.label:<24} queries={self.queries:<3} "
            f"recall={self.mean_recall:.3f} "
            f"fully={self.fully_recalled}/{self.queries}"
        )


def _gold_list(document: dict[str, Any]) -> list[dict[str, Any]]:
    return next(value for value in document.values() if isinstance(value, list))


def video_turns() -> tuple[UUID, list[dict[str, Any]]]:
    """The evidence turns whose anchors a retrieval change can move."""

    gold = json.loads(VIDEO_GOLD.read_text())
    turns = [
        turn
        for conversation in gold["conversations"]
        for turn in conversation["turns"]
        if turn.get("expected_evidence")
        and turn.get("expected_route") == "evidence_qa"
        and any(a.get("role") == "required" for a in turn["expected_evidence"])
    ]
    return UUID(gold["lecture"]["video_id"]), turns


def video_recall(
    connection: Connection,
    *,
    owner_id: str | UUID,
    dimension: int,
    label: str,
    limit: int = 8,
) -> RecallReport:
    """Anchor recall over the frozen lecture, through the real retriever."""

    video_id, turns = video_turns()
    embedder = OpenRouterTextEmbedder(dimension=dimension)
    scores = []
    for turn in turns:
        query = turn.get("expected_standalone_query") or turn["user"]
        _, found = retrieve_video_evidence(
            connection,
            owner_id=owner_id,
            video_id=video_id,
            query=query,
            limit=limit,
            text_embedder=embedder,
        )
        scores.append(anchor_recall(turn["expected_evidence"], list(found)))
    return RecallReport(
        label=label,
        queries=len(scores),
        mean_recall=statistics.mean(scores) if scores else 0.0,
        fully_recalled=sum(1 for score in scores if score == 1.0),
    )


def book_recall(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_id: int,
    dimension: int,
    label: str,
    limit: int = 8,
) -> RecallReport:
    """Node recall over the book gold set.

    The set names its expected sections by path rather than by id, which is
    what lets it be pointed at a re-ingested copy of the same book: the ids
    change on every ingest and the table of contents does not.
    """

    gold = _gold_list(json.loads(BOOK_GOLD.read_text()))
    embedder = OpenRouterEmbedder(
        "openai/text-embedding-3-large", dimension=dimension
    )
    by_path = {
        row["path_text"]: row["id"]
        for row in connection.execute(
            "select id, path_text from nodes where book_id = %s", (book_id,)
        ).fetchall()
    }

    scores = []
    for item in gold:
        if not item.get("answerable", True):
            continue
        required = {
            by_path[anchor["path"]]
            for anchor in item["expected_evidence"]
            if anchor.get("role") == "required" and anchor["path"] in by_path
        }
        if not required:
            continue
        hits = retrieve_book(
            connection,
            item["query"],
            mode="hybrid",
            owner_id=owner_id,
            book_id=book_id,
            limit=limit,
            embedder=embedder,
        )
        found = {hit.source_node_id for hit in hits}
        scores.append(len(required & found) / len(required))
    return RecallReport(
        label=label,
        queries=len(scores),
        mean_recall=statistics.mean(scores) if scores else 0.0,
        fully_recalled=sum(1 for score in scores if score == 1.0),
    )


def binary_rescore_recall(
    connection: Connection,
    *,
    dimension: int,
    depths: tuple[int, ...] = (20, 50, 100, 200),
    limit: int = 10,
) -> dict[int, float]:
    """Recall of binary-code search plus a rescoring pass, against exact search.

    Not how anything is deployed. This measures the option: keep binary codes
    small enough to stay resident and touch the full vectors only for the
    candidates a Hamming search already shortlisted. Reported per rescoring
    depth, because the depth is the whole trade.
    """

    _, turns = video_turns()
    embedder = OpenRouterTextEmbedder(dimension=dimension)
    vectors = [
        str(list(embedder.embed_query(
            turn.get("expected_standalone_query") or turn["user"]
        ).vectors[0]))
        for turn in turns
    ]

    # The dimension is interpolated rather than bound: a type modifier such as
    # bit(1024) must be a constant, and Postgres rejects a parameter there.
    # It is an int this function owns, never anything a caller supplies.
    width = int(dimension)
    scope = (
        "from video.evidence_embeddings "
        f"where embedding_kind = 'text' and dimension = {width}"
    )

    def exact(vector: str) -> set[str]:
        rows = connection.execute(
            f"select evidence_id {scope} order by embedding <=> %s::halfvec limit %s",
            (vector, limit),
        ).fetchall()
        return {row["evidence_id"] for row in rows}

    def rescored(vector: str, depth: int) -> set[str]:
        rows = connection.execute(
            f"""
            select evidence_id from (
                select evidence_id, embedding {scope}
                order by binary_quantize(embedding::vector)::bit({width})
                         <~> binary_quantize(%s::vector)::bit({width})
                limit %s
            ) shortlist
            order by shortlist.embedding <=> %s::halfvec
            limit %s
            """,
            (vector, int(depth), vector, limit),
        ).fetchall()
        return {row["evidence_id"] for row in rows}

    results: dict[int, float] = {}
    for depth in depths:
        scores = []
        for vector in vectors:
            truth = exact(vector)
            scores.append(len(truth & rescored(vector, depth)) / max(len(truth), 1))
        results[depth] = statistics.mean(scores) if scores else 0.0
    return results
