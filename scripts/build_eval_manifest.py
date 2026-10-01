"""Build a small five-flow suite from frozen datasets, without inference."""
import argparse
import json
from pathlib import Path
import re

from evals.suite import Case, Manifest

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "evaluation/five_flow_manifest.json"


def build():
    book = json.loads((ROOT / "evaluation/multiturn_gold.json").read_text())
    video = json.loads((ROOT / "evaluation/video_gold.json").read_text())
    candidate = json.loads((ROOT / "evaluation/interview_candidate_profiles.json").read_text())
    ideal = json.loads((ROOT / "evaluation/ideal_interview_flow_seed.json").read_text())
    sheets = json.loads((ROOT / "evaluation/revision_sheet_gold.json").read_text())
    wanted = {f"mt-{c:03}-t{t}" for c, turns in [(1, [1, 2, 3, 4]), (2, [2]), (3, [1, 2, 3, 4]),
               (4, [1]), (5, [1, 2, 3, 4]), (6, [1]), (8, [1, 2, 3, 4]), (9, [1, 2]),
               (10, [1]), (11, [1, 2])] for t in turns}
    cases = []
    for conversation in book["conversations"]:
        selected = [i for i, t in enumerate(conversation["turns"]) if t["turn_id"] in wanted]
        if not selected:
            continue
        previous = None
        for turn in conversation["turns"][:max(selected) + 1]:
            flow = "summary" if turn["expected_route"] == "hierarchy_summary" else "chat"
            points = [point.strip() for point in re.split(r"(?<=[.!?])\s+", re.sub(r"\[N\d+:P\d+\]", "", turn["reference_answer"])) if point.strip()]
            cases.append(Case(id=turn["turn_id"], flow=flow, adapter="book", title=turn["user"],
                              tier="synthetic_model_adjudicated", inputs={"dataset": "evaluation/multiturn_gold.json",
                              "conversation_id": conversation["id"], "turn_id": turn["turn_id"], "question": turn["user"]},
                              expected={**turn, "coverage_points": points},
                              source={"kind": "book", "file_hash": book["book"]["source_file_sha256"], "title": book["book"]["title"]},
                              depends_on=[previous] if previous else [],
                              aspects=(["complete_scope", "concepts", "figures_equations", "grounding", "efficiency"] if flow == "summary" else
                                       ["input", "grounding", "efficiency"] + (["context"] if previous else []) +
                                       (["retrieval"] if turn["expected_route"] == "retrieval_qa" else []) +
                                       (["abstention"] if not turn.get("answerable", True) else []))))
            previous = turn["turn_id"]
    for conversation_id in ("vc-002", "vc-003", "vc-004", "vc-005", "vc-006", "vc-007", "vc-011", "vc-012"):
        conversation = next(c for c in video["conversations"] if c["id"] == conversation_id)
        turn = conversation["turns"][0]
        cases.append(Case(id=turn["turn_id"], flow="video_course", adapter="video", title=turn["user"],
                          tier="author_labelled_video_gold", inputs={"dataset": "evaluation/video_gold.json",
                          "conversation_id": conversation_id, "turn_id": turn["turn_id"], "question": turn["user"]},
                          expected=turn, source={"kind": "video", **video["lecture"]},
                          aspects=["lecture", "multimodal", "temporal", "grounding", "efficiency"]))
    course_cases = [
        ("course-synthesis", "Compare the role of RPC, GFS and Raft across this course. Cite evidence from each relevant lecture.",
         ["RPC provides remote request/response communication.", "GFS addresses distributed storage.", "Raft addresses replicated consensus.", "Distinguish storage replication from consensus and cite separate lectures."]),
        ("course-exclusion", "Explain RPC and thread-based concurrency using only the RPC lecture.",
         ["Explain request/response and concurrency from the selected lecture.", "Excluded lectures must not appear as sources."]),
        ("course-unanswerable", "What exact password did the instructor use for their personal database?",
         ["Admit insufficient source evidence without inventing a password."]),
        ("course-followup", "How do those mechanisms handle failures? Keep the earlier comparison and cite the relevant lectures.",
         ["Retain the RPC/GFS/Raft comparison from the preceding answer.", "Distinguish their failure handling and cite source evidence."]),
    ]
    for id_, question, points in course_cases:
        cases.append(Case(id=id_, flow="video_course", adapter="course", title=question,
                          tier="author_labelled_human_review_pending", inputs={"question": question,
                          "lecture_title_filter": "RPC" if id_ == "course-exclusion" else None},
                          expected={"coverage_points": points, "answerable": id_ != "course-unanswerable"},
                          source={"kind": "course", "title": "MIT 6.824 Distributed Systems (Spring 2020)"},
                          depends_on=["course-synthesis"] if id_ == "course-followup" else [],
                          aspects=["course", "source_exclusion", "history", "abstention", "grounding", "efficiency"]))
    for chapter in (1, 3, 6, 8, 10):
        criteria = next(item["criteria"] for item in sheets["chapters"] if item["chapter"] == chapter)
        cases.append(Case(id=f"sheet-chapter-{chapter}", flow="revision_sheet", adapter="sheet",
                          title=f"Revision sheet — chapter {chapter}", tier=sheets["review_status"],
                          inputs={"chapter": chapter, "dataset": "evaluation/revision_sheet_gold.json"},
                          expected={"coverage_points": [item["point"] for item in criteria], "concept_sources": criteria,
                                    "layout": "A4 readable, no clipping"},
                          source={"kind": "book", "file_hash": book["book"]["source_file_sha256"], "title": book["book"]["title"]},
                          aspects=["scope", "concepts", "grounding", "figures", "layout", "repair", "efficiency"]))
    for profile in candidate["profiles"]:
        cases.append(Case(id=profile["id"], flow="interview", adapter="interview_grade", title=f"Candidate assessment — {profile['id']}",
                          tier="human_authored_candidate_authored_evidence", inputs={"dataset": "evaluation/interview_candidate_profiles.json", "profile_id": profile["id"]},
                          expected=profile, source={"kind": "authored_scenario", "scenario_id": profile["scenario_id"]},
                          aspects=["grading", "grounding", "adaptation", "efficiency"]))
    for case in ideal["cases"]:
        cases.append(Case(id="ideal-" + case["id"], flow="interview", adapter="ideal", title=f"Ideal dialogue — {case['id']}",
                          tier="synthetic_human_review_pending", inputs={"dataset": "evaluation/ideal_interview_flow_seed.json", "case_id": case["id"]},
                          expected=case["reference_outputs"], source={"kind": "authored_scenario", "scenario_id": case["id"]},
                          aspects=["question_generation", "ideal", "grounding", "efficiency"]))
    requirements = {}
    definitions = {
        "chat": ("input context retrieval grounding abstention fallback journey efficiency".split(),
                 ["tests/test_multiturn_evaluation.py", "tests/test_source_first_evaluation.py", "tests/test_conversation.py"]),
        "summary": ("complete_scope concepts figures_equations overflow repair journey efficiency".split(),
                    ["tests/test_study_summary.py"]),
        "video_course": ("lecture course multimodal temporal source_version source_exclusion ingestion_recovery journey efficiency".split(),
                         ["tests/test_video_evaluation.py", "tests/test_video_courses.py", "tests/test_course_query_embedding_cost.py", "tests/test_video_worker.py"]),
        "revision_sheet": ("scope concepts grounding figures layout repair followup job_recovery journey efficiency".split(),
                           ["tests/test_revision_sheets.py", "tests/test_revision_resilience.py", "tests/test_revision_review.py", "tests/test_revision_pagination.py"]),
        "interview": ("question_generation grading adaptation clarification coding ideal voice report journey efficiency".split(),
                      ["tests/test_interviews.py", "tests/test_ideal_interviews.py", "tests/test_livekit_voice.py", "tests/test_livekit_worker.py"]),
    }
    for flow, (aspects, tests) in definitions.items():
        for aspect in aspects:
            requirements[f"{flow}.{aspect}"] = {"flow": flow, "cases": [case.id for case in cases if case.flow == flow and aspect in case.aspects],
                                                "contract_tests": tests, "contract_status": "not_run_by_suite",
                                                "journey_status": "not_run_by_suite", "human_review": "pending",
                                                "note": "Candidate cases target this aspect; mapping alone is not proof. Use contracts, connected journeys and artifact review separately."}
    for aspect, tests in {"budget": ["tests/test_eval_budget.py"], "evaluator_integrity": ["tests/test_eval_integrity.py"],
                          "ownership": ["tests/test_book_source.py"], "stream_recovery": ["tests/test_operations_telemetry.py"],
                          "browser_accessibility": []}.items():
        requirements[f"shared.{aspect}"] = {"flow": "shared", "cases": [], "contract_tests": tests,
                                             "contract_status": "not_run_by_suite", "journey_status": "not_run_by_suite"}
    return Manifest(version="five-flow-v1", cases=cases, requirements=requirements)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = build().model_dump_json(indent=2) + "\n"
    if args.check:
        if not TARGET.exists() or TARGET.read_text() != value:
            raise SystemExit("Evaluation manifest is stale")
    else:
        TARGET.write_text(value)
    print(f"{len(build().cases)} cases; manifest {'current' if args.check else 'written'}")


if __name__ == "__main__":
    main()
