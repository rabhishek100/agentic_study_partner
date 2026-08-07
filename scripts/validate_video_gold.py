"""Check the video gold set against itself and against canonical Postgres.

A gold set can be wrong in two ways that no evaluation run will ever report.
It can be internally inconsistent — an unanswerable turn carrying required
evidence, a rewrite probe on an independent turn — and it can ask for evidence
the lecture does not contain, which scores the system zero for a mistake the
dataset made. The video pilot already hit the second kind once, when a
truncated evidence inventory silently dropped three chapters from question
generation and the run reported a higher score for the smaller set.

So every anchor is checked to fall inside the lecture and to have at least one
evidence unit of a listed modality overlapping it. That is not a relevance
judgment — it cannot tell whether the stretch answers the question — but it
does prove the stretch is reachable, which is the precondition for the recall
number meaning anything.
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from storage.database import connection, environment_owner_id, parse_owner_id


DEFAULT_GOLD = Path("evaluation/video_gold.json")
ROUTES = {
    "evidence_qa",
    "lecture_summary",
    "topic_inventory",
    "prior_answer_transform",
    "clarify",
}
DEPENDENCIES = {"independent", "dependent", "ambiguous"}
MODALITIES = {"transcript", "visual_frame", "visual_event", "resource_page"}


def _structural(dataset: dict) -> list[str]:
    problems: list[str] = []
    seen_turns: set[str] = set()
    for conversation in dataset["conversations"]:
        for index, turn in enumerate(conversation["turns"]):
            turn_id = turn["turn_id"]
            where = f"{turn_id}"
            if turn_id in seen_turns:
                problems.append(f"{where}: duplicate turn id")
            seen_turns.add(turn_id)
            if not turn_id.startswith(conversation["id"]):
                problems.append(f"{where}: turn id does not belong to its conversation")
            if turn["expected_route"] not in ROUTES:
                problems.append(f"{where}: unknown route {turn['expected_route']}")
            if turn["history_dependency"] not in DEPENDENCIES:
                problems.append(f"{where}: unknown history dependency")
            if index == 0 and turn["history_dependency"] != "independent":
                problems.append(
                    f"{where}: the first turn has no history to depend on"
                )
            answerable = turn.get("answerable", True)
            anchors = turn.get("expected_evidence") or []
            if not answerable and anchors:
                problems.append(
                    f"{where}: an unanswerable turn must not carry required evidence"
                )
            if not answerable and not turn.get("near_miss_evidence"):
                problems.append(
                    f"{where}: an unanswerable turn needs near misses, or it only "
                    "tests empty retrieval rather than false confidence"
                )
            if turn.get("rewrite_probe"):
                if turn["history_dependency"] != "dependent":
                    problems.append(
                        f"{where}: a rewrite probe must be a dependent follow-up"
                    )
                if index == 0:
                    problems.append(f"{where}: a rewrite probe cannot be turn one")
                if not turn.get("expected_standalone_query"):
                    problems.append(
                        f"{where}: a rewrite probe needs the gold rewrite to "
                        "compare the router's own against"
                    )
            if turn.get("requires_visual_evidence") and not any(
                set(anchor["modalities"]) & {"visual_frame", "visual_event"}
                for anchor in anchors
            ):
                problems.append(
                    f"{where}: marked as needing visual evidence but no visual anchor"
                )
            if not turn.get("reference_answer", "").strip():
                problems.append(f"{where}: no reference answer")
            if turn.get("requires_document_evidence") and not any(
                anchor.get("resource_pages") for anchor in anchors
            ):
                problems.append(
                    f"{where}: marked as needing the linked document but no "
                    "page anchor"
                )
            for anchor in anchors + (turn.get("near_miss_evidence") or []):
                pages = anchor.get("resource_pages")
                if pages is None:
                    if anchor["start_ms"] >= anchor["end_ms"]:
                        problems.append(
                            f"{where}: anchor span is empty or reversed"
                        )
                elif not pages or any(page < 1 for page in pages):
                    problems.append(f"{where}: page anchor names no usable page")
                elif set(anchor["modalities"]) != {"resource_page"}:
                    problems.append(
                        f"{where}: a page anchor can only be satisfied by the "
                        "linked document, so it must name that modality alone"
                    )
                unknown = set(anchor["modalities"]) - MODALITIES
                if unknown:
                    problems.append(
                        f"{where}: unknown modality {', '.join(sorted(unknown))}"
                    )
                if not anchor.get("why", "").strip():
                    problems.append(f"{where}: anchor has no stated reason")
    return problems


def _canonical(dataset: dict, *, owner_id, database_url: str | None) -> list[str]:
    lecture = dataset["lecture"]
    problems: list[str] = []
    with connection(database_url, readonly=True) as database:
        video = database.execute(
            """
            select v.id, v.duration_ms, v.readiness_status,
                   v.current_ingestion_version_id as version_id
            from video.videos as v
            where v.owner_id = %s and v.id = %s
            """,
            (owner_id, lecture["video_id"]),
        ).fetchone()
        if video is None:
            return [
                f"lecture {lecture['video_id']} is not present for this owner; "
                "run against the database the set was authored on"
            ]
        if video["readiness_status"] not in {"ready", "degraded"}:
            problems.append(
                f"lecture is {video['readiness_status']}, so it cannot be asked "
                "anything and every turn would fail for that reason alone"
            )
        if video["duration_ms"] != lecture["duration_ms"]:
            problems.append(
                f"duration drifted: gold {lecture['duration_ms']}, "
                f"canonical {video['duration_ms']}"
            )
        version_id = video["version_id"]

        for conversation in dataset["conversations"]:
            for turn in conversation["turns"]:
                anchors = (turn.get("expected_evidence") or []) + (
                    turn.get("near_miss_evidence") or []
                )
                for anchor in anchors:
                    if anchor.get("resource_pages"):
                        reachable = database.execute(
                            """
                            select count(*) as count
                            from video.evidence_units
                            where owner_id = %s and video_id = %s
                              and ingestion_version_id = %s
                              and modality = 'resource_page'
                              and page_number = any(%s)
                            """,
                            (
                                owner_id,
                                lecture["video_id"],
                                version_id,
                                list(anchor["resource_pages"]),
                            ),
                        ).fetchone()["count"]
                        if reachable != len(set(anchor["resource_pages"])):
                            problems.append(
                                f"{turn['turn_id']}: pages "
                                f"{anchor['resource_pages']} are not all "
                                "indexed for this version"
                            )
                        continue
                    if anchor["end_ms"] > video["duration_ms"]:
                        problems.append(
                            f"{turn['turn_id']}: anchor ends after the lecture does"
                        )
                        continue
                    reachable = database.execute(
                        """
                        select count(*) as count
                        from video.evidence_units
                        where owner_id = %s and video_id = %s
                          and ingestion_version_id = %s
                          and modality = any(%s)
                          and start_ms is not null
                          and start_ms < %s
                          and coalesce(end_ms, start_ms) > %s
                        """,
                        (
                            owner_id,
                            lecture["video_id"],
                            version_id,
                            list(anchor["modalities"]),
                            anchor["end_ms"],
                            anchor["start_ms"],
                        ),
                    ).fetchone()["count"]
                    if not reachable:
                        problems.append(
                            f"{turn['turn_id']}: no "
                            f"{'/'.join(anchor['modalities'])} evidence exists "
                            f"between {anchor['start_ms']} and {anchor['end_ms']}, "
                            "so this anchor can never be recalled"
                        )
    return problems


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--database-url")
    parser.add_argument("--owner-id")
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help="Skip the canonical checks, which need the lecture's database.",
    )
    args = parser.parse_args()

    dataset = json.loads(args.gold.read_text(encoding="utf-8"))
    turns = sum(len(item["turns"]) for item in dataset["conversations"])
    problems = _structural(dataset)
    if not args.structure_only:
        owner_id = (
            parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
        )
        problems += _canonical(
            dataset, owner_id=owner_id, database_url=args.database_url
        )

    print(
        f"{dataset['dataset_id']} v{dataset['version']}: "
        f"{len(dataset['conversations'])} conversations, {turns} turns, "
        f"review status {dataset['review']['status']}"
    )
    if problems:
        print(f"\n{len(problems)} problems:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("No problems found. This is a structural check, not a relevance review.")


if __name__ == "__main__":
    main()
