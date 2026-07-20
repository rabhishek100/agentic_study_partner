"""Validate conversation gold data against canonical SQLite."""

import argparse
import json
from pathlib import Path
import re
import sqlite3


CITATION = re.compile(r"\[N(\d+):P(\d+)]")
ROUTES = {
    "hierarchy_summary",
    "hierarchy_list",
    "retrieval_qa",
    "prior_answer_transform",
    "clarify",
    "abstain",
}
DEPENDENCIES = {"independent", "dependent", "ambiguous"}
ROLES = {"required", "supporting", "optional_recap"}


def _canonical(connection, book_id):
    book = connection.execute(
        "SELECT * FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    nodes = {
        row["id"]: dict(row)
        for row in connection.execute(
            "SELECT * FROM nodes WHERE book_id = ?", (book_id,)
        )
    }
    return book, nodes


def validate(gold: dict, connection: sqlite3.Connection, *, allow_pending=False):
    """Return all structural and canonical-data errors in one pass."""

    errors = []
    book_id = gold.get("book", {}).get("database_book_id")
    book, nodes = _canonical(connection, book_id)
    if not book:
        errors.append(f"book {book_id!r} does not exist")

    conversations = gold.get("conversations", [])
    seen_conversations = set()
    seen_turns = set()
    unanswerable = 0
    for conversation in conversations:
        cid = conversation.get("id")
        if not cid or cid in seen_conversations:
            errors.append(f"invalid or duplicate conversation id: {cid!r}")
        seen_conversations.add(cid)
        prior_turns = set()
        for turn in conversation.get("turns", []):
            turn_id = turn.get("turn_id")
            prefix = turn_id or f"{cid}/unknown"
            if not turn_id or turn_id in seen_turns:
                errors.append(f"invalid or duplicate turn id: {turn_id!r}")
            seen_turns.add(turn_id)
            if turn.get("expected_route") not in ROUTES:
                errors.append(f"{prefix}: invalid route")
            if turn.get("history_dependency") not in DEPENDENCIES:
                errors.append(f"{prefix}: invalid history dependency")
            if not all(
                isinstance(turn.get(field), str) and turn[field].strip()
                for field in ("user", "reference_answer")
            ):
                errors.append(f"{prefix}: required text field is empty")
            if (
                turn.get("expected_route")
                in {"hierarchy_summary", "hierarchy_list", "retrieval_qa"}
                and not (turn.get("expected_standalone_query") or "").strip()
            ):
                errors.append(f"{prefix}: standalone query is empty")
            missing_dependencies = set(
                turn.get("depends_on_turn_ids", [])
            ) - prior_turns
            if missing_dependencies:
                errors.append(
                    f"{prefix}: dependency is not an earlier turn: "
                    f"{sorted(missing_dependencies)}"
                )
            prior_turns.add(turn_id)

            scope = turn.get("expected_scope")
            if scope and scope.get("node_id") not in nodes:
                errors.append(f"{prefix}: scope node is not canonical")

            evidence_lookup = {}
            for label in ("expected_evidence", "near_miss_evidence"):
                for item in turn.get(label, []):
                    node = nodes.get(item.get("node_id"))
                    if node is None:
                        errors.append(
                            f"{prefix}: evidence node {item.get('node_id')} is not canonical"
                        )
                        continue
                    if label == "expected_evidence" and item.get("role") not in ROLES:
                        errors.append(f"{prefix}: invalid evidence role")
                    pages = set(item.get("pages", []))
                    if any(
                        page < node["start_page"] or page > node["end_page"]
                        for page in pages
                    ):
                        errors.append(
                            f"{prefix}: evidence page falls outside node "
                            f"{node['id']}"
                        )
                    evidence_lookup.setdefault(node["id"], set()).update(pages)

            for node_id, page in CITATION.findall(turn.get("reference_answer", "")):
                node_id, page = int(node_id), int(page)
                if page not in evidence_lookup.get(node_id, set()):
                    errors.append(
                        f"{prefix}: citation [N{node_id}:P{page}] is not in judged evidence"
                    )

            if not turn.get("answerable", True):
                unanswerable += 1
                if turn.get("expected_evidence"):
                    errors.append(
                        f"{prefix}: unanswerable turn has expected evidence"
                    )

    review = gold.get("review", {})
    if not allow_pending and review.get("status") != "model_adjudicated":
        errors.append("dataset review is not finalized")
    if not allow_pending:
        for reviewer in review.get("reviewers", []):
            if reviewer.get("decision") != "approve":
                errors.append(
                    f"reviewer {reviewer.get('reviewer_id')} did not approve"
                )

    return {
        "valid": not errors,
        "errors": errors,
        "conversation_count": len(conversations),
        "turn_count": len(seen_turns),
        "unanswerable_count": unanswerable,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=Path("evaluation/multiturn_gold.json"),
    )
    parser.add_argument(
        "--database", type=Path, default=Path("data/books.sqlite3")
    )
    parser.add_argument("--allow-pending-review", action="store_true")
    args = parser.parse_args()
    gold = json.loads(args.gold_set.read_text(encoding="utf-8"))
    connection = sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        result = validate(
            gold, connection, allow_pending=args.allow_pending_review
        )
    finally:
        connection.close()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
