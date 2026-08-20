"""The grounding ladder: which rung answered, and when the turn climbs.

The escalation the reader sees is automatic, so what has to be tested is not
that it happens but that it is *bounded, caused and recorded*: it climbs one
rung at a time, only on an abstention, never past a lock, and every step
carries the verdict that provoked it.
"""

import unittest
from uuid import uuid4

from study.contracts import (
    CitationRef,
    ConversationState,
    EvidenceRef,
    TurnDecision,
    TurnResult,
)
from study.graph import StudyGraphContext, build_study_graph
from study.grounding import GroundingPolicy, insufficiency, settled_rung

OWNER = uuid4()


def evidence(rank: int, chunk_id: str) -> EvidenceRef:
    return EvidenceRef(
        node_id=rank * 10,
        pages=[108],
        path="4.3 Class imbalance",
        chunk_id=chunk_id,
        rank=rank,
    )


def result(
    *,
    outcome: str = "answer",
    answer: str = "Accuracy scores the shortcut [S1].",
    evidence_refs: tuple[EvidenceRef, ...] = (evidence(1, "page-chunk"),),
    citation_ranks: tuple[int, ...] = (1,),
    source_type: str = "book_library",
) -> TurnResult:
    return TurnResult(
        question="Why is accuracy the wrong measure here?",
        answer=answer,
        route="retrieval_qa",
        history_dependency="independent",
        outcome=outcome,
        evidence=list(evidence_refs),
        citations=[
            CitationRef(marker=f"[S{rank}]", node_id=rank * 10, page=108, evidence_rank=rank)
            for rank in citation_ranks
        ],
        source_type=source_type,
    )


class PolicyTests(unittest.TestCase):
    def test_the_open_source_is_always_inside_the_library(self):
        # Widening must be a superset, or "widening" would drop the very book
        # the reader has open.
        policy = GroundingPolicy.for_source([7], library_book_ids=[9])
        self.assertEqual(policy.books_for("open_source"), (7,))
        self.assertEqual(policy.books_for("library"), (7, 9))

    def test_the_library_rung_is_skipped_when_it_adds_nothing(self):
        policy = GroundingPolicy.for_source([7], library_book_ids=[7])
        self.assertEqual(policy.next_rung("open_source"), "model_knowledge")

    def test_the_ladder_climbs_one_rung_at_a_time(self):
        policy = GroundingPolicy.for_source([7], library_book_ids=[7, 9])
        self.assertEqual(policy.next_rung("open_source"), "library")
        self.assertEqual(policy.next_rung("library"), "model_knowledge")
        self.assertIsNone(policy.next_rung("model_knowledge"))

    def test_the_lock_stops_the_ladder_at_the_readers_own_material(self):
        policy = GroundingPolicy.for_source(
            [7],
            library_book_ids=[7, 9],
            allow_model_knowledge=False,
        )
        self.assertEqual(policy.next_rung("open_source"), "library")
        self.assertIsNone(policy.next_rung("library"))


class InsufficiencyTests(unittest.TestCase):
    def test_an_abstention_is_the_signal_and_supplies_the_reason(self):
        reason = insufficiency(
            result(outcome="abstain", answer="Insufficient evidence: no serving guidance.")
        )
        self.assertEqual(reason, "Insufficient evidence: no serving guidance.")

    def test_an_answer_with_nothing_behind_it_is_insufficient_too(self):
        self.assertIsNotNone(insufficiency(result(evidence_refs=(), citation_ranks=())))

    def test_a_clarification_never_widens(self):
        # The turn asked the reader something. Searching more sources would
        # answer a question they have not been given the chance to ask.
        clarify = result(outcome="clarify", evidence_refs=(), citation_ranks=())
        self.assertIsNone(insufficiency(clarify))

    def test_an_error_is_not_escalated_past(self):
        failed = result(outcome="error", evidence_refs=(), citation_ranks=())
        self.assertIsNone(insufficiency(failed))


class SettledRungTests(unittest.TestCase):
    def test_citing_only_the_anchored_passage_settles_at_the_anchor(self):
        self.assertEqual(
            settled_rung(result(), "open_source", pinned_ids=["page-chunk"]),
            "anchor",
        )

    def test_citing_anything_else_stays_at_the_rung_that_searched(self):
        wider = result(
            evidence_refs=(evidence(1, "page-chunk"), evidence(2, "elsewhere")),
            citation_ranks=(1, 2),
        )
        self.assertEqual(
            settled_rung(wider, "open_source", pinned_ids=["page-chunk"]),
            "open_source",
        )

    def test_an_ungrounded_answer_reports_where_it_really_came_from(self):
        self.assertEqual(
            settled_rung(result(source_type="model_knowledge"), "library"),
            "model_knowledge",
        )
        self.assertEqual(
            settled_rung(result(source_type="web_search"), "library"),
            "web_search",
        )


class LadderRunTests(unittest.TestCase):
    """The graph itself, with planning and execution stubbed out."""

    def setUp(self):
        import study.graph as graph_module

        self.graph_module = graph_module
        self.original_analyze = graph_module.analyze_turn
        self.original_execute = graph_module.execute_decision
        self.searched: list[tuple[int, ...] | None] = []
        graph_module.analyze_turn = lambda *_, **__: TurnDecision(
            route="retrieval_qa",
            history_dependency="independent",
            standalone_query="why is accuracy the wrong measure",
            reason="a grounded question about the open page",
        )

    def tearDown(self):
        self.graph_module.analyze_turn = self.original_analyze
        self.graph_module.execute_decision = self.original_execute

    def stub_execution(self, results: list[TurnResult]) -> None:
        pending = list(results)

        def execute(question, decision, state, **kwargs):
            self.searched.append(kwargs.get("turn_book_ids"))
            produced = pending.pop(0) if pending else results[-1]
            if decision.route == "external_qa":
                return produced.model_copy(
                    update={"route": "external_qa", "source_type": "model_knowledge"}
                )
            return produced

        self.graph_module.execute_decision = execute

    def run_turn(self, policy: GroundingPolicy | None) -> TurnResult:
        graph = build_study_graph()
        output = graph.invoke(
            {
                "question": "Why is accuracy the wrong measure here?",
                "conversation": ConversationState(conversation_id="c1", book_ids=[7, 9]),
            },
            context=StudyGraphContext(owner_id=OWNER, grounding_policy=policy),
        )
        return output["result"]

    def test_without_a_policy_nothing_changes(self):
        # The main chat is measured against a frozen gold set. A turn with no
        # policy must take exactly the path it took before the ladder existed:
        # one pass, no rung, no widening, and no book override.
        self.stub_execution([result(outcome="abstain")])
        produced = self.run_turn(None)
        self.assertEqual(self.searched, [None])
        self.assertIsNone(produced.grounding_rung)
        self.assertEqual(produced.widenings, [])

    def test_the_open_source_answering_ends_the_ladder(self):
        self.stub_execution([result()])
        produced = self.run_turn(
            GroundingPolicy.for_source([7], library_book_ids=[7, 9])
        )
        self.assertEqual(self.searched, [(7,)])
        self.assertEqual(produced.widenings, [])
        self.assertEqual(produced.grounding_rung, "open_source")

    def test_an_abstention_widens_to_the_library_and_records_why(self):
        self.stub_execution(
            [
                result(outcome="abstain", answer="Insufficient evidence: not covered."),
                result(),
            ]
        )
        produced = self.run_turn(
            GroundingPolicy.for_source([7], library_book_ids=[7, 9])
        )
        # The retry really did search more than the open source.
        self.assertEqual(self.searched, [(7,), (7, 9)])
        (widening,) = produced.widenings
        self.assertEqual(widening.from_rung, "open_source")
        self.assertEqual(widening.to_rung, "library")
        self.assertIn("not covered", widening.reason)
        self.assertEqual(produced.grounding_rung, "library")
        self.assertTrue(produced.is_grounded)

    def test_a_question_neither_source_covers_leaves_the_library_last(self):
        self.stub_execution(
            [
                result(outcome="abstain", answer="Insufficient evidence: page."),
                result(outcome="abstain", answer="Insufficient evidence: library."),
                result(answer="Answered from general knowledge.", evidence_refs=(), citation_ranks=()),
            ]
        )
        produced = self.run_turn(
            GroundingPolicy.for_source([7], library_book_ids=[7, 9])
        )
        self.assertEqual(
            [(step.from_rung, step.to_rung) for step in produced.widenings],
            [("open_source", "library"), ("library", "model_knowledge")],
        )
        self.assertEqual(produced.grounding_rung, "model_knowledge")
        # The boundary the interface renders on: this answer is not grounded,
        # and nothing about it may appear as though it were.
        self.assertFalse(produced.is_grounded)

    def test_the_lock_abstains_instead_of_leaving_the_source(self):
        self.stub_execution([result(outcome="abstain", answer="Insufficient evidence.")])
        produced = self.run_turn(
            GroundingPolicy.for_source([7], allow_model_knowledge=False)
        )
        self.assertEqual(self.searched, [(7,)])
        self.assertEqual(produced.widenings, [])
        self.assertEqual(produced.outcome, "abstain")
        self.assertEqual(produced.grounding_rung, "open_source")


if __name__ == "__main__":
    unittest.main()
