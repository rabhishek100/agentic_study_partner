"""The source-first evaluation's own scoring.

Driven by a stub runner for the same reason `evals.source_first` takes one: a
metric that can only be checked by spending a model call per case is a metric
nobody checks. What is under test here is the judging — that a blended answer
fails, that anchor recall counts the right things, and that the gold set says
what it claims to.
"""

import json
import unittest
from pathlib import Path

from evals.source_first import evaluate_source_first, score_case
from study.contracts import CitationRef, EvidenceRef, TurnResult, WideningStep

GOLD = Path("evaluation/source_first_gold.json")


def evidence(rank: int, chunk_id: str) -> EvidenceRef:
    return EvidenceRef(
        node_id=rank * 10,
        pages=[100 + rank],
        path="4.3 Class imbalance",
        chunk_id=chunk_id,
        rank=rank,
    )


def turn(
    *,
    answer: str = "Accuracy scores the shortcut [S1].",
    evidence_refs=(("page-chunk",),),
    rung: str | None = "anchor",
    source_type: str = "book_library",
    widenings=(),
) -> TurnResult:
    refs = [evidence(index + 1, ids[0]) for index, ids in enumerate(evidence_refs)]
    return TurnResult(
        question="Why is accuracy the wrong measure here?",
        answer=answer,
        route="retrieval_qa",
        history_dependency="independent",
        outcome="answer",
        evidence=refs,
        citations=[
            CitationRef(marker="[S1]", node_id=10, page=101, evidence_rank=1)
        ],
        source_type=source_type,
        grounding_rung=rung,
        widenings=list(widenings),
    )


CASE = {
    "id": "sf-x",
    "question": "Why is accuracy the wrong measure here?",
    "anchor": {"kind": "document_page", "book_id": 1, "page": 108},
    "expect_rung": "anchor",
    "expect_grounded": True,
}


class ScoringTests(unittest.TestCase):
    def test_an_anchored_turn_that_did_what_it_should_has_no_failures(self):
        row = score_case(
            CASE,
            turn(),
            resolved=("page-chunk",),
            matched=True,
        )

        self.assertEqual(row.failures, ())
        self.assertEqual(row.checks["anchor_recall"], 1.0)
        self.assertTrue(row.checks["anchor_leads"])

    def test_the_anchored_passage_has_to_lead_rather_than_merely_appear(self):
        # Pinned evidence enters first and carries the lowest marker. Behind
        # retrieval it is competing with the book rather than grounding on it.
        row = score_case(
            CASE,
            turn(evidence_refs=(("elsewhere",), ("page-chunk",))),
            resolved=("page-chunk",),
            matched=True,
        )

        self.assertEqual(row.checks["anchor_recall"], 1.0)
        self.assertFalse(row.checks["anchor_leads"])

    def test_an_anchor_that_never_reached_the_evidence_fails(self):
        row = score_case(
            CASE,
            turn(evidence_refs=(("elsewhere",),)),
            resolved=("page-chunk",),
            matched=True,
        )

        self.assertEqual(row.checks["anchor_recall"], 0.0)
        self.assertIn("no anchored passage reached the evidence", row.failures)

    def test_answering_at_the_wrong_rung_fails_and_says_which(self):
        row = score_case(
            CASE,
            turn(rung="library"),
            resolved=("page-chunk",),
            matched=True,
        )

        self.assertFalse(row.checks["rung_matches"])
        self.assertIn("answered at library, expected anchor", row.failures)

    def test_an_ungrounded_answer_wearing_citations_is_a_defect(self):
        # The hard one. An answer that left the reader's sources has nothing
        # for a marker to point at, so a marker means it is claiming grounding
        # it does not have.
        row = score_case(
            {**CASE, "expect_rung": "model_knowledge", "expect_grounded": False},
            turn(
                answer="Partly — sklearn does this [S1].",
                rung="model_knowledge",
                source_type="model_knowledge",
            ),
            resolved=(),
            matched=True,
        )

        self.assertTrue(row.checks["blended"])
        self.assertIn("ungrounded answer carries citation markers", row.failures)

    def test_an_ungrounded_answer_without_markers_is_fine(self):
        row = score_case(
            {**CASE, "expect_rung": "model_knowledge", "expect_grounded": False},
            turn(
                answer="Partly. It sets the inverse-frequency weight.",
                rung="model_knowledge",
                source_type="model_knowledge",
            ),
            resolved=(),
            matched=True,
        )

        self.assertFalse(row.checks["blended"])
        self.assertEqual(row.failures, ())

    def test_a_widening_without_a_reason_fails(self):
        # Escalation is automatic, so a widening that records no cause turns
        # the ladder back into a claim.
        row = score_case(
            {**CASE, "expect_rung": "library"},
            turn(
                rung="library",
                widenings=(
                    WideningStep(from_rung="open_source", to_rung="library", reason=" "),
                ),
            ),
            resolved=("page-chunk",),
            matched=True,
        )

        self.assertIn("a widening was recorded without a reason", row.failures)

    def test_an_expected_resolution_failure_is_not_a_failure(self):
        row = score_case(
            {**CASE, "expect_selection_resolves": False},
            turn(),
            resolved=("page-chunk",),
            matched=False,
        )

        self.assertFalse(row.checks["selection_resolved"])
        self.assertEqual(row.failures, ())


class RunTests(unittest.TestCase):
    def test_a_run_summarises_rates_and_names_every_blend(self):
        cases = [
            {**CASE, "id": "a"},
            {
                **CASE,
                "id": "b",
                "expect_rung": "model_knowledge",
                "expect_grounded": False,
            },
        ]
        answers = {
            "a": turn(),
            "b": turn(
                answer="From general knowledge [S1].",
                rung="model_knowledge",
                source_type="model_knowledge",
            ),
        }
        order = iter(["a", "b"])

        summary = evaluate_source_first(
            cases,
            resolve=lambda anchor: (True, ("page-chunk",)),
            run_turn=lambda question, anchor, stay_in_source: answers[next(order)],
        )

        self.assertEqual(summary["cases"], 2)
        self.assertEqual(summary["blended"], ["b"])
        self.assertEqual(summary["selection_resolution_rate"], 1.0)

    def test_a_case_that_raises_is_reported_rather_than_lost(self):
        def explode(*_args, **_kwargs):
            raise RuntimeError("no model configured")

        summary = evaluate_source_first(
            [CASE],
            resolve=lambda anchor: (True, ("page-chunk",)),
            run_turn=explode,
        )

        self.assertEqual(summary["errors"], ["sf-x"])
        self.assertEqual(summary["cases"], 1)


class GoldSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gold = json.loads(GOLD.read_text())

    def test_every_case_states_what_it_expects(self):
        for case in self.gold["cases"]:
            with self.subTest(case=case["id"]):
                self.assertIn("question", case)
                self.assertIn("anchor", case)
                self.assertIn("rationale", case)
                self.assertTrue(
                    case.get("expect_rung") or case.get("expect_selection_resolves") is not None,
                    "a case must expect a rung or a resolution outcome",
                )

    def test_the_set_covers_every_rung_the_ladder_can_settle_at(self):
        # A set that only contains questions the book answers measures nothing
        # about escalation, which is the behaviour most likely to be wrong.
        rungs = {case.get("expect_rung") for case in self.gold["cases"]}
        self.assertIn("anchor", rungs)
        self.assertIn("open_source", rungs)
        self.assertIn("model_knowledge", rungs)

    def test_the_set_says_it_has_not_been_reviewed(self):
        # The expected rungs are a proposal until someone who knows the library
        # confirms them, and the artifact has to carry that rather than imply
        # an authority it does not have.
        self.assertFalse(self.gold["review_provenance"]["human_reviewed"])

    def test_the_anchors_are_the_shape_the_resolver_takes(self):
        from study.contracts import parse_anchor

        for case in self.gold["cases"]:
            with self.subTest(case=case["id"]):
                anchor = parse_anchor({**case["anchor"], "anchor_id": "gold"})
                self.assertTrue(anchor.book_id > 0)


if __name__ == "__main__":
    unittest.main()
