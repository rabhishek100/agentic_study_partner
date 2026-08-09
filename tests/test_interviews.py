"""Adaptive interview planning, grounding, graph routing, and speech."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from decks.topics import ScopeInventory, Topic
from interviews.contracts import (
    AnswerEvaluation,
    InterviewCheckpoint,
    InterviewQuestion,
    InterviewSession,
    InterviewTurn,
    ScoreCard,
    TopicState,
)
from interviews.evaluation import (
    InterviewValidationError,
    resolve_citations,
    sanitize_evaluation,
    validate_question,
)
from interviews.graph import AnswerGraphContext, answer_graph
from interviews.planning import detect_format, estimate_duration
from interviews.speech import (
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
    synthesize_interviewer_speech,
)


def topic(*, evidence: str = "[N7:P42]\nLogistic regression models log odds.") -> Topic:
    return Topic(
        key="node:7",
        ordinal=0,
        label="Logistic regression",
        required=True,
        evidence_text=evidence,
        allowed_markers=frozenset({"[N7:P42]"}),
        node_id=7,
        start_page=42,
        end_page=45,
    )


def inventory(*, title: str = "Logistic regression", evidence: str | None = None):
    item = topic(evidence=evidence or "[N7:P42]\nLogistic regression models log odds.")
    return ScopeInventory(
        source_kind="book",
        scope_key="book:1:node:7",
        title=title,
        source_title="Machine Learning",
        outline=f"- {item.label}",
        topics=(item,),
    )


def question() -> InterviewQuestion:
    return InterviewQuestion(
        topic_key="node:7",
        topic_label="Logistic regression",
        text="What does logistic regression model?",
        expected_points=["Connect probability to log odds."],
        suggested_answer="It models log odds as a linear function. [N7:P42]",
        citation_markers=["[N7:P42]"],
        difficulty="mid",
    )


def evaluation(*, complete: bool, clarify: bool = False) -> AnswerEvaluation:
    return AnswerEvaluation(
        classification="source_aligned" if complete else "partially_correct",
        scores=ScoreCard(
            technical_correctness=5 if complete else 2,
            depth_completeness=4 if complete else 2,
            reasoning_structure=4 if complete else 3,
            tradeoff_awareness=3,
            communication_clarity=4,
            independence=5,
        ),
        strengths=["Clear definition"],
        gaps=[] if complete else ["Connect probability to log odds"],
        concise_feedback="Grounded feedback. [N7:P42]",
        recommended_answer="It models log odds linearly. [N7:P42]",
        citation_markers=["[N7:P42]"],
        needs_clarifying_probe=clarify,
        clarifying_probe=("How do probability and log odds connect?" if clarify else None),
        topic_complete=complete,
    )


def session() -> InterviewSession:
    return InterviewSession(
        session_id="00000000-0000-0000-0000-000000000001",
        source_kind="book",
        book_id=1,
        node_id=7,
        scope_key="book:1:node:7",
        title="Interview · Logistic regression",
        source_title="Machine Learning",
        interview_format="concept",
        format_source="detected",
        feedback_mode="realistic",
        target_level="mid",
        maximum_duration_minutes=30,
        estimated_min_minutes=10,
        estimated_max_minutes=15,
        status="active",
        elapsed_seconds=30,
        checkpoint=InterviewCheckpoint(
            topics=[TopicState(key="node:7", label="Logistic regression")],
            active_topic_key="node:7",
        ),
        turns=[],
    )


class RawResponse:
    response_metadata = {"cost": 0.001}


class FakeStructuredModel:
    def __init__(self, value) -> None:
        self.value = value

    def invoke(self, messages):
        return {"parsed": self.value, "raw": RawResponse()}


class PlanningTests(unittest.TestCase):
    def test_detects_source_led_before_generic_system_design(self) -> None:
        scope = inventory(
            title="A system design interview walkthrough",
            evidence=(
                "[N7:P42]\nBegin the interview by clarifying requirements, "
                "then estimate throughput and discuss failure modes."
            ),
        )
        self.assertEqual(detect_format(scope), "source_led")

    def test_detects_system_design_from_repeated_source_vocabulary(self) -> None:
        scope = inventory(
            title="Scalable architecture",
            evidence=(
                "[N7:P42]\nArchitecture requirements include latency and "
                "throughput trade-offs and distributed failure modes."
            ),
        )
        self.assertEqual(detect_format(scope), "system_design")

    def test_duration_is_an_estimate_capped_at_two_hours(self) -> None:
        many = inventory()
        many = ScopeInventory(
            source_kind=many.source_kind,
            scope_key=many.scope_key,
            title=many.title,
            source_title=many.source_title,
            outline=many.outline,
            topics=tuple(
                Topic(
                    key=f"node:{index}",
                    ordinal=index,
                    label=f"Topic {index}",
                    required=True,
                    evidence_text=f"[N{index}:P42]\nEvidence",
                    allowed_markers=frozenset({f"[N{index}:P42]"}),
                )
                for index in range(100)
            ),
        )
        self.assertEqual(estimate_duration(many, target_level="senior"), (120, 120))


class GroundingTests(unittest.TestCase):
    def test_question_private_answer_must_stay_inside_topic(self) -> None:
        invalid = question().model_copy(
            update={
                "suggested_answer": "A claim. [N9:P99]",
                "citation_markers": ["[N9:P99]"],
            }
        )
        with self.assertRaises(InterviewValidationError):
            validate_question(invalid, topic())

    def test_malformed_evaluation_falls_back_to_validated_model_answer(self) -> None:
        invalid = evaluation(complete=True).model_copy(
            update={
                "recommended_answer": "Unsupported. [N9:P99]",
                "citation_markers": ["[N9:P99]"],
            }
        )
        repaired = sanitize_evaluation(invalid, question=question(), topic=topic())
        self.assertEqual(repaired.recommended_answer, question().suggested_answer)
        self.assertEqual(repaired.citation_markers, ["[N7:P42]"])

    def test_markers_resolve_to_reader_facing_pages(self) -> None:
        citations = resolve_citations(["[N7:P42]"], topic())
        self.assertEqual(citations[0].page, 42)
        self.assertEqual(citations[0].node_id, 7)


class GraphTests(unittest.TestCase):
    def test_strong_answer_finishes_when_source_is_exhausted(self) -> None:
        current = InterviewTurn(turn_index=0, question=question())
        output = answer_graph.invoke(
            {
                "session": session(),
                "inventory": inventory(),
                "current_turn": current,
                "answer_text": "It models the log odds as a linear function.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(evaluation(complete=True))
            ),
        )
        self.assertIsNone(output["next_question"])
        self.assertEqual(output["checkpoint"].coverage_ratio, 1.0)
        self.assertIn("covered", output["finish_reason"])
        self.assertEqual(output["evaluation_cost_usd"], 0.001)

    def test_ambiguous_weak_answer_gets_one_clarifying_probe(self) -> None:
        current = InterviewTurn(turn_index=0, question=question())
        output = answer_graph.invoke(
            {
                "session": session(),
                "inventory": inventory(),
                "current_turn": current,
                "answer_text": "It predicts a class.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(
                    evaluation(complete=False, clarify=True)
                )
            ),
        )
        self.assertEqual(output["next_question"].kind, "clarifying")
        self.assertEqual(
            output["next_question"].text,
            "How do probability and log odds connect?",
        )

    def test_no_new_question_starts_near_the_duration_ceiling(self) -> None:
        timed = session().model_copy(update={"elapsed_seconds": 30 * 60 - 20})
        output = answer_graph.invoke(
            {
                "session": timed,
                "inventory": inventory(),
                "current_turn": InterviewTurn(turn_index=0, question=question()),
                "answer_text": "It predicts a class.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(evaluation(complete=False))
            ),
        )
        self.assertIsNone(output["next_question"])
        self.assertIn("duration ceiling", output["finish_reason"])


class SpeechTests(unittest.TestCase):
    def test_kokoro_is_the_default_and_cost_is_character_bounded(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"mp3", headers={"content-type": "audio/mpeg"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch.dict("os.environ", {}, clear=False):
                result = synthesize_interviewer_speech("Why logistic regression?", client=client)

        body = requests[0].read().decode()
        self.assertIn(DEFAULT_TTS_MODEL, body)
        self.assertIn(DEFAULT_TTS_VOICE, body)
        self.assertEqual(result.content, b"mp3")
        self.assertLess(result.cost_usd, 0.001)


if __name__ == "__main__":
    unittest.main()
