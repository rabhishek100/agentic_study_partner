"""The two measurements the video gold set exists to make.

Both are about the gap between a check passing and the thing it stands for
being true, so the tests are written as that gap: a summary that satisfies the
runtime's coverage rule and says nothing, and a follow-up whose rewrite is the
only reason its evidence is found.
"""

import json
from pathlib import Path
import unittest

from evals.video import anchor_recall, evaluate_video_conversations
from evals.video_coverage import distinctive_terms, measure_summary_substance
from video.contracts import VideoEvidenceRef
from video.lecture import CoverageUnit, LectureScope


GOLD = Path("evaluation/video_gold.json")


def window(rank: int, text: str, start_ms: int, end_ms: int) -> VideoEvidenceRef:
    return VideoEvidenceRef(
        rank=rank,
        evidence_id=f"w{rank}",
        modality="transcript",
        excerpt=text,
        retrieval_method="complete_transcript",
        score=1.0,
        start_ms=start_ms,
        end_ms=end_ms,
    )


TOKENIZATION = (
    "So this part is called tokenization and what it entails is cutting the "
    "text with respect to some arbitrary unit of text. The unit of text is "
    "called a token which is why the method is called tokenization. Another "
    "way would be to just separate by words, and subword tokenizers leverage "
    "the roots of words so bear and bears share a particle."
)
RECURRENCE = (
    "So we are going to talk about RNNs which stands for recurrent neural "
    "network. What RNNs do is they keep a hidden representation of the "
    "sentence so far and consider tokens one at a time. The hidden state is "
    "sometimes called a context vector, and the vanishing gradient problem "
    "comes from backpropagating that product through time."
)


def scope() -> LectureScope:
    return LectureScope(
        version_id=__import__("uuid").uuid4(),
        windows=[
            window(1, TOKENIZATION, 0, 300_000),
            window(2, RECURRENCE, 300_000, 600_000),
        ],
        duration_ms=600_000,
    )


UNITS = (
    CoverageUnit(key="window:1", label="0:00–5:00", ranks=(1,), window_ranks=(1,), required=True),
    CoverageUnit(key="window:2", label="5:00–10:00", ranks=(2,), window_ranks=(2,), required=True),
)


class SummarySubstanceTests(unittest.TestCase):
    def test_a_stretch_can_be_cited_without_being_covered(self) -> None:
        """The failure this measurement was built for.

        Both sentences satisfy `video.lecture.evaluate_coverage`, which asks
        only whether a marker pointing at the window appears. Neither says
        anything a reader could use, and the second is the exact shape a
        coverage-repair addendum produces when it is graded on citing.
        """

        vacuous = (
            "The lecture opens with introductory material [S1]. "
            "It then continues with further discussion [S2]."
        )
        measured = measure_summary_substance(vacuous, scope=scope(), units=UNITS)

        self.assertEqual(measured.cited_rate, 1.0)
        self.assertEqual(measured.substantive_rate, 0.0)
        self.assertEqual(len(measured.vacuous_units), 2)
        self.assertEqual(measured.summary()["coverage_overstatement"], 1.0)

    def test_a_summary_that_says_what_happened_passes_both(self) -> None:
        substantive = (
            "Text is cut into tokens, and subword tokenizers share the roots "
            "of words so that bear and bears are not unrelated [S1]. "
            "Recurrent networks keep a hidden state across the sequence, "
            "which is where the vanishing gradient problem comes from [S2]."
        )
        measured = measure_summary_substance(substantive, scope=scope(), units=UNITS)

        self.assertEqual(measured.cited_rate, 1.0)
        self.assertEqual(measured.substantive_rate, 1.0)
        self.assertEqual(measured.vacuous_units, ())

    def test_a_stretch_nobody_cited_is_neither_cited_nor_substantive(self) -> None:
        measured = measure_summary_substance(
            "Text is cut into tokens, and subword tokenizers share the roots "
            "of words so that bear and bears are not unrelated [S1].",
            scope=scope(),
            units=UNITS,
        )
        self.assertEqual(measured.cited_rate, 0.5)
        self.assertEqual(measured.substantive_rate, 0.5)
        # Not vacuous: nothing was claimed about it, so nothing overstated it.
        self.assertEqual(measured.vacuous_units, ())

    def test_substance_credit_does_not_transfer_between_stretches(self) -> None:
        """Talking about window one while citing window two is not coverage."""

        misattributed = (
            "The lecture explains tokenization, tokens, and the subword "
            "roots of words at length [S2]."
        )
        measured = measure_summary_substance(
            misattributed, scope=scope(), units=UNITS
        )
        substance = {unit.key: unit.substantive for unit in measured.units}
        self.assertFalse(substance["window:2"])

    def test_a_marker_at_the_end_of_a_paragraph_cites_the_paragraph(self) -> None:
        """The regression that made this measurement's first run meaningless.

        Real summaries cite once per paragraph, after the final full stop.
        Attributing the marker to a sentence leaves it standing behind the
        empty string, and every summary ever written scores zero.
        """

        paragraph_cited = (
            "Text is cut into tokens with respect to an arbitrary unit. "
            "Subword tokenizers leverage the roots of words, so bear and "
            "bears share a particle rather than being unrelated. [S1]\n\n"
            "Recurrent networks keep a hidden representation of the sentence "
            "so far, sometimes called a context vector. [S2]"
        )
        measured = measure_summary_substance(
            paragraph_cited, scope=scope(), units=UNITS
        )
        self.assertEqual(measured.substantive_rate, 1.0)

    def test_adjacent_markers_both_stand_behind_the_same_claim(self) -> None:
        both = (
            "Text is cut into tokens by subword tokenizers using the roots of "
            "words, and a recurrent network then keeps a hidden context "
            "vector across the sentence. [S1][S2]"
        )
        measured = measure_summary_substance(both, scope=scope(), units=UNITS)
        self.assertEqual(measured.cited_rate, 1.0)
        self.assertEqual(measured.substantive_rate, 1.0)

    def test_distinctive_terms_exclude_the_lecture_s_own_background(self) -> None:
        terms = distinctive_terms(UNITS, scope())
        # "token" appears in both stretches, so it identifies neither.
        self.assertNotIn("token", terms["window:1"])
        self.assertIn("tokenization", terms["window:1"])
        self.assertIn("hidden", terms["window:2"])


class AnchorRecallTests(unittest.TestCase):
    """Evidence is matched on when it happened, not on which row it is."""

    ANCHOR = [
        {
            "role": "required",
            "start_ms": 100_000,
            "end_ms": 200_000,
            "modalities": ["transcript"],
            "why": "",
        }
    ]

    def test_partial_overlap_counts(self) -> None:
        # Retrieval windows and gold spans are cut on different boundaries, so
        # requiring containment would fail evidence a reader would use.
        self.assertEqual(
            anchor_recall(self.ANCHOR, [window(1, "x", 180_000, 260_000)]), 1.0
        )

    def test_touching_the_edge_is_not_overlapping(self) -> None:
        self.assertEqual(
            anchor_recall(self.ANCHOR, [window(1, "x", 200_000, 260_000)]), 0.0
        )

    def test_the_wrong_modality_does_not_satisfy_the_anchor(self) -> None:
        visual = window(1, "x", 120_000, 140_000).model_copy(
            update={"modality": "visual_frame"}
        )
        self.assertEqual(anchor_recall(self.ANCHOR, [visual]), 0.0)

    def test_a_turn_with_no_required_anchors_is_fully_covered(self) -> None:
        # Whole-lecture routes and abstentions are judged on behaviour, and a
        # zero here would silently drag the reported recall down.
        self.assertEqual(anchor_recall([], []), 1.0)


class RewriteAblationTests(unittest.TestCase):
    """The replay that decides whether rewriting earns its call."""

    class Runner:
        """A stub lecture where only the rewritten query finds the evidence."""

        connection = None
        owner_id = "00000000-0000-4000-8000-000000000001"
        video_id = "11111111-1111-4111-8111-111111111111"
        video_title = "stub"

        def __init__(self):
            self.retrieved = []

        def __call__(self, question, state):
            from video.contracts import VideoTurnResult
            from video.conversation import record_turn

            result = VideoTurnResult(
                question=question,
                answer=f"Answer about {question} [S1]",
                route="evidence_qa",
                history_dependency=(
                    "dependent" if state.messages else "independent"
                ),
                standalone_query=(
                    "What is the downside of character-level tokenization?"
                    if state.messages
                    else question
                ),
                evidence=[window(1, "tokenization", 100_000, 200_000)],
                citations=[],
                outcome="answer",
            )
            return result, record_turn(state, question, result)

        def retrieve(self, query):
            self.retrieved.append(query)
            if "character-level" in query:
                return [window(1, "character level", 1_621_000, 1_685_000)]
            return [window(1, "unrelated", 10_000, 20_000)]

    CONVERSATION = [
        {
            "id": "stub-001",
            "title": "stub",
            "turns": [
                {
                    "turn_id": "stub-001-t1",
                    "user": "What is tokenization?",
                    "expected_route": "evidence_qa",
                    "history_dependency": "independent",
                    "answerable": True,
                    "expected_evidence": [],
                    "reference_answer": "",
                },
                {
                    "turn_id": "stub-001-t2",
                    "user": "What's the downside of the last one?",
                    "expected_route": "evidence_qa",
                    "history_dependency": "dependent",
                    "answerable": True,
                    "rewrite_probe": True,
                    "expected_standalone_query": (
                        "What is the downside of character-level tokenization?"
                    ),
                    "expected_evidence": [
                        {
                            "role": "required",
                            "start_ms": 1_621_000,
                            "end_ms": 1_685_000,
                            "modalities": ["transcript"],
                            "why": "",
                        }
                    ],
                    "reference_answer": "",
                },
            ],
        }
    ]

    def test_each_arm_is_retrieved_and_compared(self) -> None:
        runner = self.Runner()
        evaluation = evaluate_video_conversations(self.CONVERSATION, runner)
        ablation = evaluation["rewrite_ablation"]

        self.assertEqual(ablation["probes"], 1)
        self.assertEqual(ablation["mean_recall"]["raw"], 0.0)
        self.assertEqual(ablation["mean_recall"]["rewritten"], 1.0)
        self.assertEqual(ablation["mean_recall"]["gold"], 1.0)
        self.assertEqual(ablation["rewrite_helped"], 1)
        self.assertEqual(ablation["share_of_gold_gain"], 1.0)
        # Three retrievals for one probe, and none for the independent turn:
        # an ablation arm on a turn with nothing to resolve measures noise.
        self.assertEqual(len(runner.retrieved), 3)

    def test_the_ablation_can_be_skipped(self) -> None:
        runner = self.Runner()
        evaluation = evaluate_video_conversations(
            self.CONVERSATION, runner, run_rewrite_ablation=False
        )
        self.assertEqual(evaluation["rewrite_ablation"], {"probes": 0})
        self.assertEqual(runner.retrieved, [])


class GoldSetTests(unittest.TestCase):
    def test_the_frozen_set_covers_what_it_claims_to(self) -> None:
        dataset = json.loads(GOLD.read_text(encoding="utf-8"))
        turns = [
            turn
            for conversation in dataset["conversations"]
            for turn in conversation["turns"]
        ]
        routes = {turn["expected_route"] for turn in turns}

        self.assertIn("lecture_summary", routes)
        self.assertIn("topic_inventory", routes)
        self.assertIn("prior_answer_transform", routes)
        self.assertGreaterEqual(
            sum(1 for turn in turns if turn.get("rewrite_probe")), 8
        )
        self.assertGreaterEqual(
            sum(1 for turn in turns if not turn.get("answerable", True)), 3
        )
        self.assertGreaterEqual(
            sum(1 for turn in turns if turn.get("requires_visual_evidence")), 3
        )
        self.assertTrue(
            any(turn.get("measures_summary_coverage") for turn in turns)
        )

    def test_every_anchor_falls_inside_the_lecture(self) -> None:
        dataset = json.loads(GOLD.read_text(encoding="utf-8"))
        duration = dataset["lecture"]["duration_ms"]
        for conversation in dataset["conversations"]:
            for turn in conversation["turns"]:
                anchors = (turn.get("expected_evidence") or []) + (
                    turn.get("near_miss_evidence") or []
                )
                for anchor in anchors:
                    with self.subTest(turn=turn["turn_id"]):
                        if anchor.get("resource_pages"):
                            # The deck has no timestamps, so its anchors name
                            # pages. Nothing aligns a page with a moment, and
                            # ingestion deliberately refuses to invent one.
                            self.assertTrue(
                                all(
                                    page >= 1
                                    for page in anchor["resource_pages"]
                                )
                            )
                            continue
                        self.assertLess(anchor["start_ms"], anchor["end_ms"])
                        self.assertLessEqual(anchor["end_ms"], duration)


if __name__ == "__main__":
    unittest.main()
