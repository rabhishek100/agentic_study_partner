"""Validate the frozen multi-turn gold set against canonical SQLite."""

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re
import sqlite3


CITATION = re.compile(r"\[N(\d+):P(\d+)]")
HIERARCHY_REFERENCE = re.compile(
    r"\[node (\d+), PDF p\. (\d+)]"
)
ROUTES = {
    "hierarchy_summary",
    "hierarchy_list",
    "retrieval_qa",
    "prior_answer_transform",
    "clarify",
    "abstain",
}
HISTORY_DEPENDENCIES = {"independent", "dependent", "ambiguous"}
SCOPE_BEHAVIORS = {
    "hard_filter",
    "prefer_scope",
    "global",
    "reuse_prior_answer",
    "clarify",
}
STATE_UPDATES = {"set_active_scope", "retain", "clear", "none"}
PENDING_CLARIFICATION_UPDATES = {"set", "clear", "none"}
EVIDENCE_ROLES = {"required", "supporting", "optional_recap"}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate multi-turn judgments against canonical storage."
    )
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=Path("evaluation/multiturn_gold.json"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/books.sqlite3"),
    )
    parser.add_argument(
        "--allow-pending-review",
        action="store_true",
        help="Skip finalized-review checks while reviewers are still working.",
    )
    return parser


def _canonical(connection: sqlite3.Connection, book_id: int) -> tuple[dict, dict]:
    book = dict(
        connection.execute(
            "SELECT * FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()
        or {}
    )
    nodes = {}
    for row in connection.execute(
        """
        SELECT
            nodes.*,
            GROUP_CONCAT(DISTINCT content_blocks.page_number) AS content_pages
        FROM nodes
        LEFT JOIN content_blocks ON content_blocks.node_id = nodes.id
        WHERE nodes.book_id = ?
        GROUP BY nodes.id
        """,
        (book_id,),
    ):
        node = dict(row)
        node["content_pages"] = {
            int(page)
            for page in (node["content_pages"] or "").split(",")
            if page
        }
        nodes[node["id"]] = node
    return book, nodes


def _descendants(connection: sqlite3.Connection, node_id: int) -> set[int]:
    rows = connection.execute(
        """
        WITH RECURSIVE subtree(id) AS (
            SELECT id FROM nodes WHERE id = ?
            UNION ALL
            SELECT nodes.id
            FROM nodes
            JOIN subtree ON nodes.parent_id = subtree.id
        )
        SELECT id FROM subtree
        """,
        (node_id,),
    )
    return {row[0] for row in rows}


def _direct_children(
    connection: sqlite3.Connection,
    node_id: int,
) -> list[int]:
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT id
            FROM nodes
            WHERE parent_id = ?
            ORDER BY toc_index
            """,
            (node_id,),
        )
    ]


def _summary_evidence_nodes(
    connection: sqlite3.Connection,
    nodes: dict[int, dict],
    node_id: int,
) -> tuple[set[int], set[int]]:
    content_nodes = {
        descendant_id
        for descendant_id in _descendants(connection, node_id)
        if nodes[descendant_id]["content_pages"]
    }
    recap_nodes = {
        descendant_id
        for descendant_id in content_nodes
        if nodes[descendant_id]["title"].strip().casefold()
        in {"summary", "conclusion"}
    }
    return content_nodes - recap_nodes, recap_nodes


def _chapter_id(
    connection: sqlite3.Connection,
    nodes: dict[int, dict],
    node_id: int,
) -> int:
    node = nodes[node_id]
    if node["node_type"] == "chapter":
        return node_id
    row = connection.execute(
        """
        WITH RECURSIVE ancestors AS (
            SELECT id, parent_id, node_type FROM nodes WHERE id = ?
            UNION ALL
            SELECT nodes.id, nodes.parent_id, nodes.node_type
            FROM nodes
            JOIN ancestors ON ancestors.parent_id = nodes.id
        )
        SELECT id FROM ancestors WHERE node_type = 'chapter'
        """,
        (node_id,),
    ).fetchone()
    return row[0] if row else 0


def validate(gold: dict, connection: sqlite3.Connection, *, allow_pending: bool) -> dict:
    errors: list[str] = []
    seen_ids: set[str] = set()
    book_gold = gold["book"]
    book, nodes = _canonical(connection, book_gold["database_book_id"])
    if not book:
        errors.append("gold-set book does not exist")
    else:
        expected_book_fields = {
            "title": book["title"],
            "author": book["author"],
            "source_filename": book["source_filename"],
            "source_file_sha256": book["file_hash"],
            "parser_version": book["parser_version"],
        }
        for field, canonical_value in expected_book_fields.items():
            if book_gold[field] != canonical_value:
                errors.append(
                    f"book.{field} differs from canonical storage: "
                    f"{book_gold[field]!r} != {canonical_value!r}"
                )

    if gold.get("schema_version") != "1.0.0":
        errors.append("schema_version must be 1.0.0")
    if gold.get("set_id") != "designing-ml-systems-multiturn-v1":
        errors.append("unexpected set_id")
    if gold.get("judgment_unit") != "toc_node_and_pdf_page":
        errors.append("judgment_unit must be toc_node_and_pdf_page")
    if gold.get("page_reference") != "source_pdf_page_number":
        errors.append("page_reference must be source_pdf_page_number")
    if gold.get("citation_format") != "[N<node_id>:P<pdf_page>]":
        errors.append("citation_format must be [N<node_id>:P<pdf_page>]")
    if (
        gold.get("hierarchy_reference_format")
        != "[node <node_id>, PDF p. <page>]"
    ):
        errors.append("invalid hierarchy_reference_format")

    provenance = gold.get("provenance", {})
    expected_provenance = {
        "dataset_origin": "synthetic",
        "canonical_database": "data/books.sqlite3",
        "source_file_sha256": book.get("file_hash"),
        "parser_version": book.get("parser_version"),
        "human_verified": False,
    }
    for field, expected in expected_provenance.items():
        if provenance.get(field) != expected:
            errors.append(
                f"provenance.{field} differs from expected value: "
                f"{provenance.get(field)!r} != {expected!r}"
            )

    if allow_pending:
        if gold["status"] != "review_pending":
            errors.append("pending dataset status must be review_pending")
        if gold.get("dataset_tier") != "synthetic_candidate":
            errors.append("pending dataset tier must be synthetic_candidate")
        if gold.get("frozen_on") is not None:
            errors.append("pending dataset must not have frozen_on")
        if gold["review"]["status"] != "independent_review_pending":
            errors.append(
                "pending review.status must be independent_review_pending"
            )
    else:
        if gold["status"] != "frozen":
            errors.append("final dataset status must be frozen")
        if gold.get("dataset_tier") != "synthetic_model_adjudicated":
            errors.append(
                "final dataset tier must be synthetic_model_adjudicated"
            )
        if not gold.get("frozen_on"):
            errors.append("final dataset requires frozen_on")
        review = gold["review"]
        if review["status"] != "model_adjudicated":
            errors.append("review.status must be model_adjudicated")
        reviewers = review["reviewers"]
        if len(reviewers) < 2:
            errors.append("at least two reviewer decisions are required")
        if any(reviewer.get("decision") != "approve" for reviewer in reviewers):
            errors.append("every recorded reviewer must approve the final set")
        reviewer_ids = [
            reviewer.get("reviewer_id")
            for reviewer in reviewers
        ]
        if len(set(reviewer_ids)) != len(reviewer_ids):
            errors.append("reviewer IDs must be unique")
        context_ids = [
            reviewer.get("context_id")
            for reviewer in reviewers
        ]
        if (
            any(not context_id for context_id in context_ids)
            or len(set(context_ids)) != len(context_ids)
        ):
            errors.append(
                "reviewers require unique separate context IDs"
            )
        required_reviewer_fields = {
            "reviewer_id",
            "context_id",
            "role",
            "decision",
            "reviewed_on",
            "findings",
            "conversation_ids",
        }
        reviewed_conversations: set[str] = set()
        for reviewer in reviewers:
            missing_fields = required_reviewer_fields - reviewer.keys()
            if missing_fields:
                errors.append(
                    f"reviewer {reviewer.get('reviewer_id')!r} lacks "
                    f"{sorted(missing_fields)}"
                )
            if reviewer.get("review_context") != "separate":
                errors.append(
                    f"reviewer {reviewer.get('reviewer_id')!r} must declare "
                    "review_context=separate"
                )
            if not reviewer.get("findings"):
                errors.append(
                    f"reviewer {reviewer.get('reviewer_id')!r} lacks findings"
                )
            reviewed_conversations.update(
                reviewer.get("conversation_ids", [])
            )
        conversation_ids = {
            conversation["id"]
            for conversation in gold["conversations"]
        }
        if reviewed_conversations != conversation_ids:
            errors.append(
                "reviewer conversation coverage must equal the full set"
            )
        adjudication = review.get("adjudication", {})
        if adjudication.get("decision") != "accepted":
            errors.append("review.adjudication.decision must be accepted")
        for field in (
            "adjudicator_id",
            "rationale",
            "adjudicated_on",
        ):
            if not adjudication.get(field):
                errors.append(
                    f"review.adjudication.{field} is required"
                )
        dated_events = [
            reviewer.get("reviewed_on")
            for reviewer in reviewers
        ] + [adjudication.get("adjudicated_on")]
        try:
            frozen_on = date.fromisoformat(gold["frozen_on"])
            if any(
                frozen_on < date.fromisoformat(event)
                for event in dated_events
                if event
            ):
                errors.append(
                    "frozen_on must be on or after reviews and adjudication"
                )
        except (TypeError, ValueError):
            errors.append("review and freeze dates must use ISO YYYY-MM-DD")

    route_counts: Counter[str] = Counter()
    dependency_counts: Counter[str] = Counter()
    chapter_counts: Counter[int] = Counter()
    turn_count = 0
    answerable_count = 0

    for conversation in gold["conversations"]:
        conversation_id = conversation["id"]
        if conversation_id in seen_ids:
            errors.append(f"duplicate id: {conversation_id}")
        seen_ids.add(conversation_id)
        if not allow_pending and conversation["review_status"] != "accepted":
            errors.append(f"{conversation_id} is not accepted")

        prior_turn_ids: set[str] = set()
        prior_turns: dict[str, dict] = {}
        active_scope_node_id: int | None = None
        clarification_pending = False
        for ordinal, turn in enumerate(conversation["turns"], start=1):
            turn_count += 1
            turn_id = turn["turn_id"]
            prefix = turn_id
            if turn_id in seen_ids:
                errors.append(f"duplicate id: {turn_id}")
            seen_ids.add(turn_id)
            if turn_id != f"{conversation_id}-t{ordinal}":
                errors.append(f"{prefix}: noncontiguous or mismatched turn ID")

            route = turn["expected_route"]
            dependency = turn["history_dependency"]
            scope_behavior = turn["scope_behavior"]
            state_update = turn["state_update"]
            clarification_update = turn.get(
                "pending_clarification_update",
                "none",
            )
            dependencies = turn.get("depends_on_turn_ids", [])
            route_counts[route] += 1
            dependency_counts[dependency] += 1
            answerable_count += int(turn["answerable"])
            if route not in ROUTES:
                errors.append(f"{prefix}: unsupported route {route!r}")
            if dependency not in HISTORY_DEPENDENCIES:
                errors.append(f"{prefix}: invalid history dependency")
            if scope_behavior not in SCOPE_BEHAVIORS:
                errors.append(f"{prefix}: invalid scope behavior")
            if state_update not in STATE_UPDATES:
                errors.append(f"{prefix}: invalid state update")
            if clarification_update not in PENDING_CLARIFICATION_UPDATES:
                errors.append(f"{prefix}: invalid clarification-state update")
            if dependency == "dependent" and not dependencies:
                errors.append(f"{prefix}: dependent turn lacks dependencies")
            if dependency != "dependent" and dependencies:
                errors.append(
                    f"{prefix}: non-dependent turn declares dependencies"
                )
            unknown_dependencies = set(dependencies) - prior_turn_ids
            if unknown_dependencies:
                errors.append(
                    f"{prefix}: dependencies are not earlier turns in the "
                    f"conversation: {sorted(unknown_dependencies)}"
                )
            if ordinal == 1 and dependency == "dependent":
                errors.append(
                    f"{prefix}: first turn cannot depend on missing history"
                )
            if dependency == "ambiguous" and route != "clarify":
                errors.append(
                    f"{prefix}: ambiguous dependency must route to clarify"
                )
            if clarification_update == "set" and route != "clarify":
                errors.append(
                    f"{prefix}: only a clarification route may set pending state"
                )
            if (
                clarification_update == "clear"
                and not clarification_pending
            ):
                errors.append(
                    f"{prefix}: cannot clear absent pending clarification"
                )

            scope = turn["expected_scope"]
            if scope is not None:
                node = nodes.get(scope["node_id"])
                if node is None:
                    errors.append(f"{prefix}: scope node does not exist")
                else:
                    is_chapter = node["node_type"] == "chapter"
                    if scope["kind"] not in {"chapter", "section"}:
                        errors.append(f"{prefix}: invalid scope kind")
                    elif (scope["kind"] == "chapter") != is_chapter:
                        errors.append(f"{prefix}: scope kind disagrees with node")
                    chapter_counts[
                        _chapter_id(connection, nodes, node["id"])
                    ] += 1

            expected_pairs: set[tuple[int, int]] = set()
            required_nodes: set[int] = set()
            expected_nodes: set[int] = set()
            evidence_roles: dict[int, str] = {}
            for evidence in turn["expected_evidence"]:
                role = evidence["role"]
                if role not in EVIDENCE_ROLES:
                    errors.append(
                        f"{prefix}: invalid evidence role {role!r}"
                    )
                if evidence["node_id"] in expected_nodes:
                    errors.append(
                        f"{prefix}: duplicate expected evidence node "
                        f"{evidence['node_id']}"
                    )
                expected_nodes.add(evidence["node_id"])
                evidence_roles[evidence["node_id"]] = role
                node = nodes.get(evidence["node_id"])
                if node is None:
                    errors.append(
                        f"{prefix}: evidence node {evidence['node_id']} missing"
                    )
                    continue
                if role == "required":
                    required_nodes.add(evidence["node_id"])
                for page in evidence["pages"]:
                    pair = (evidence["node_id"], page)
                    expected_pairs.add(pair)
                    if page not in node["content_pages"]:
                        errors.append(
                            f"{prefix}: {pair} has no canonical content block"
                        )
            near_miss_nodes: set[int] = set()
            for evidence in turn["near_miss_evidence"]:
                if not evidence.get("reason_not_sufficient"):
                    errors.append(
                        f"{prefix}: near miss lacks reason_not_sufficient"
                    )
                if evidence["node_id"] in near_miss_nodes:
                    errors.append(
                        f"{prefix}: duplicate near-miss node "
                        f"{evidence['node_id']}"
                    )
                near_miss_nodes.add(evidence["node_id"])
                node = nodes.get(evidence["node_id"])
                if node is None:
                    errors.append(
                        f"{prefix}: near-miss node {evidence['node_id']} missing"
                    )
                    continue
                for page in evidence["pages"]:
                    if page not in node["content_pages"]:
                        errors.append(
                            f"{prefix}: near miss "
                            f"({evidence['node_id']}, {page}) has no content"
                        )
            overlap = expected_nodes & near_miss_nodes
            if overlap:
                errors.append(
                    f"{prefix}: expected and near-miss evidence overlap at "
                    f"nodes {sorted(overlap)}"
                )

            citations = {
                (int(node_id), int(page))
                for node_id, page in CITATION.findall(
                    turn["reference_answer"]
                )
            }
            unexpected = citations.difference(expected_pairs)
            if unexpected:
                errors.append(
                    f"{prefix}: citations outside expected evidence "
                    f"{sorted(unexpected)}"
                )
            cited_nodes = {node_id for node_id, _ in citations}
            missing_required = required_nodes.difference(cited_nodes)
            if missing_required:
                errors.append(
                    f"{prefix}: reference answer omits required nodes "
                    f"{sorted(missing_required)}"
                )

            if turn["answerable"]:
                if (
                    not turn["expected_evidence"]
                    and route != "hierarchy_list"
                ):
                    errors.append(f"{prefix}: answerable turn lacks evidence")
                if (
                    route not in {"clarify", "abstain", "hierarchy_list"}
                    and not citations
                ):
                    errors.append(f"{prefix}: grounded answer has no citations")
            else:
                if turn["expected_evidence"]:
                    errors.append(
                        f"{prefix}: unanswerable turn has expected evidence"
                    )
                if citations:
                    errors.append(
                        f"{prefix}: unanswerable/clarify response cites evidence"
                    )
                if route not in {"clarify", "abstain"}:
                    errors.append(
                        f"{prefix}: unanswerable turn has route {route!r}"
                    )

            if route in {"hierarchy_summary", "hierarchy_list"}:
                if scope is None or scope_behavior != "hard_filter":
                    errors.append(
                        f"{prefix}: hierarchy route requires hard scope"
                    )
                elif not {
                    evidence["node_id"]
                    for evidence in turn["expected_evidence"]
                }.issubset(_descendants(connection, scope["node_id"])):
                    errors.append(
                        f"{prefix}: hierarchy evidence escapes its subtree"
                    )
            if route == "hierarchy_summary" and scope is not None:
                substantive, recaps = _summary_evidence_nodes(
                    connection,
                    nodes,
                    scope["node_id"],
                )
                required = {
                    node_id
                    for node_id, role in evidence_roles.items()
                    if role == "required"
                }
                optional_recaps = {
                    node_id
                    for node_id, role in evidence_roles.items()
                    if role == "optional_recap"
                }
                if required != substantive:
                    errors.append(
                        f"{prefix}: summary required nodes differ from the "
                        f"content-bearing substantive subtree: expected "
                        f"{sorted(substantive)}, got {sorted(required)}"
                    )
                if optional_recaps != recaps:
                    errors.append(
                        f"{prefix}: summary optional recaps differ from "
                        f"canonical recap nodes: expected {sorted(recaps)}, "
                        f"got {sorted(optional_recaps)}"
                    )
                for evidence in turn["expected_evidence"]:
                    node_pages = nodes[evidence["node_id"]]["content_pages"]
                    if set(evidence["pages"]) != node_pages:
                        errors.append(
                            f"{prefix}: summary pages for node "
                            f"{evidence['node_id']} must equal all canonical "
                            f"content pages {sorted(node_pages)}"
                        )
            if route == "hierarchy_list":
                outline_nodes = turn.get("expected_outline_node_ids", [])
                if not outline_nodes:
                    errors.append(f"{prefix}: hierarchy list lacks outline")
                elif scope is not None and not set(outline_nodes).issubset(
                    _descendants(connection, scope["node_id"])
                ):
                    errors.append(
                        f"{prefix}: outline nodes escape their scope"
                    )
                elif scope is not None and outline_nodes != _direct_children(
                    connection,
                    scope["node_id"],
                ):
                    errors.append(
                        f"{prefix}: outline must equal canonical direct "
                        "children in TOC order"
                    )
                hierarchy_references = [
                    (int(node_id), int(page))
                    for node_id, page in HIERARCHY_REFERENCE.findall(
                        turn["reference_answer"]
                    )
                ]
                expected_references = [
                    (node_id, nodes[node_id]["start_page"])
                    for node_id in outline_nodes
                ]
                if hierarchy_references != expected_references:
                    errors.append(
                        f"{prefix}: hierarchy references must contain every "
                        "direct child exactly once with canonical start page "
                        "and in TOC order"
                    )
            if route == "prior_answer_transform":
                if dependency != "dependent" or scope_behavior != "reuse_prior_answer":
                    errors.append(f"{prefix}: invalid transformation contract")
                if len(dependencies) != 1:
                    errors.append(
                        f"{prefix}: transformation requires one source turn"
                    )
                elif dependencies[0] in prior_turns:
                    source_turn = prior_turns[dependencies[0]]
                    source_pairs = {
                        (evidence["node_id"], page)
                        for evidence in source_turn["expected_evidence"]
                        for page in evidence["pages"]
                    }
                    if not expected_pairs.issubset(source_pairs):
                        errors.append(
                            f"{prefix}: transformed evidence is not a subset "
                            "of its source turn"
                        )
                    source_citations = {
                        (int(node_id), int(page))
                        for node_id, page in CITATION.findall(
                            source_turn["reference_answer"]
                        )
                    }
                    if not citations.issubset(source_citations):
                        errors.append(
                            f"{prefix}: transformed citations are not a "
                            "subset of its source answer"
                        )
            if route == "clarify" and scope_behavior != "clarify":
                errors.append(f"{prefix}: clarification must not guess scope")
            if scope_behavior == "global" and scope is not None:
                errors.append(f"{prefix}: global search has a hard scope")
            if route not in {"prior_answer_transform", "clarify"} and not turn[
                "expected_standalone_query"
            ]:
                errors.append(f"{prefix}: missing standalone query")

            if scope_behavior == "prefer_scope":
                scope_node_id = scope["node_id"] if scope else None
                if scope_node_id != active_scope_node_id:
                    errors.append(
                        f"{prefix}: preferred scope does not match active scope"
                    )
            if state_update == "set_active_scope":
                if scope is None:
                    errors.append(
                        f"{prefix}: cannot set active scope without scope"
                    )
                else:
                    active_scope_node_id = scope["node_id"]
            elif state_update == "clear":
                if active_scope_node_id is None:
                    errors.append(
                        f"{prefix}: cannot clear absent active scope"
                    )
                active_scope_node_id = None
            if clarification_update == "set":
                clarification_pending = True
            elif clarification_update == "clear":
                clarification_pending = False

            prior_turn_ids.add(turn_id)
            prior_turns[turn_id] = turn

        if clarification_pending:
            errors.append(
                f"{conversation_id}: conversation ends with pending "
                "clarification"
            )

    return {
        "valid": not errors,
        "errors": errors,
        "conversation_count": len(gold["conversations"]),
        "turn_count": turn_count,
        "answerable_count": answerable_count,
        "unanswerable_count": turn_count - answerable_count,
        "route_counts": dict(sorted(route_counts.items())),
        "history_dependency_counts": dict(sorted(dependency_counts.items())),
        "scoped_chapter_counts": dict(sorted(chapter_counts.items())),
    }


def main() -> None:
    args = build_argument_parser().parse_args()
    gold = json.loads(args.gold_set.read_text(encoding="utf-8"))
    connection = sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        result = validate(
            gold,
            connection,
            allow_pending=args.allow_pending_review,
        )
    finally:
        connection.close()
    print(json.dumps(result, indent=2))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
