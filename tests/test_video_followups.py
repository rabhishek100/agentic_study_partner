"""Follow-up behaviour: what a later turn may lean on, and what it may not."""

import json
import unittest
from uuid import uuid4

from video.analyze import analyze_turn, resolve_clarification
from video.contracts import (
    VideoConversationState,
    VideoCitationRef,
    VideoEvidenceRef,
    VideoMessage,
    VideoTurnResult,
)
from video.conversation import new_video_conversation_state, record_turn
from video.prompts import build_answer_messages


def evidence(rank: int = 1, **overrides) -> VideoEvidenceRef:
    return VideoEvidenceRef(
        rank=rank,
        evidence_id=f"{rank:064d}",
        modality=overrides.pop("modality", "transcript"),
        excerpt=overrides.pop("excerpt", "The lecturer defines attention"),
        retrieval_method="fts",
        score=0.5,
        start_ms=overrides.pop("start_ms", 60_000),
        end_ms=90_000,
        **overrides,
    )


class FakeDecisionModel:
    """Returns a scripted decision and records the payload it was given."""

    def __init__(self, **decision) -> None:
        self.decision = decision
        self.payloads: list[dict] = []

    def invoke(self, messages, config=None):
        self.payloads.append(json.loads(messages[1]["content"]))

        class Decision:
            route = self.decision.get("route", "evidence_qa")
            history_dependency = self.decision.get(
                "history_dependency", "independent"
            )
            standalone_query = self.decision.get("standalone_query")
            clarification_question = self.decision.get("clarification_question")
            reason = self.decision.get("reason", "scripted")

        return Decision()


def conversation(*, previous_answer: str | None = None, **fields):
    state = new_video_conversation_state(video_id=uuid4())
    state.messages.extend(
        [
            VideoMessage(role="user", content="What is attention?"),
            VideoMessage(
                role="assistant", content=previous_answer or "It weights values [S1]."
            ),
        ]
    )
    state.previous_answer = previous_answer or "It weights values [S1]."
    for key, value in fields.items():
        setattr(state, key, value)
    return state


class ClarificationRepairTests(unittest.TestCase):
    def test_a_short_reply_completes_the_message_that_was_unclear(self) -> None:
        state = conversation(pending_clarification="Which one do you mean?")

        decision = resolve_clarification("the second one", state)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.route, "evidence_qa")
        self.assertEqual(decision.history_dependency, "dependent")
        self.assertIn("Which one do you mean", decision.standalone_query)
        self.assertIn("the second one", decision.standalone_query)

    def test_a_new_self_contained_question_supersedes_a_stale_request(self) -> None:
        state = conversation(pending_clarification="Which one do you mean?")

        self.assertIsNone(
            resolve_clarification(
                "How does scaled dot-product attention actually compute weights?",
                state,
            )
        )

    def test_nothing_to_repair_without_a_pending_clarification(self) -> None:
        self.assertIsNone(resolve_clarification("the second one", conversation()))

    def test_the_repair_runs_before_the_control_model(self) -> None:
        state = conversation(pending_clarification="Which diagram?")
        # Not a raising stub: analyze_turn deliberately swallows control-model
        # failures and retrieves the question as asked, which would make this
        # pass whether or not the repair ran. A recording stub distinguishes
        # "repaired" from "fell back".
        model = FakeDecisionModel(standalone_query="the one at 12:00")

        decision = analyze_turn("the one at 12:00", state, model=model)

        self.assertEqual(model.payloads, [])
        self.assertEqual(decision.route, "evidence_qa")
        self.assertEqual(decision.history_dependency, "dependent")
        # Only the repair carries the unclear message forward.
        self.assertIn("Which diagram", decision.standalone_query)
        self.assertIn("the one at 12:00", decision.standalone_query)


class RouteGuardTests(unittest.TestCase):
    def test_a_self_contained_question_is_never_clarified(self) -> None:
        model = FakeDecisionModel(
            route="clarify",
            clarification_question="Which part did you mean?",
            history_dependency="ambiguous",
        )

        decision = analyze_turn(
            "How does the lecturer justify scaling by the square root of the key "
            "dimension?",
            conversation(),
            model=model,
        )

        self.assertEqual(decision.route, "evidence_qa")
        self.assertTrue(decision.standalone_query)

    def test_a_short_ambiguous_message_may_still_be_clarified(self) -> None:
        model = FakeDecisionModel(
            route="clarify",
            clarification_question="Which one?",
            history_dependency="independent",
        )

        decision = analyze_turn("that one", conversation(), model=model)

        self.assertEqual(decision.route, "clarify")
        # A clarification is ambiguous by definition, whatever the model said.
        self.assertEqual(decision.history_dependency, "ambiguous")

    def test_a_question_leaning_on_the_last_answer_is_marked_dependent(self) -> None:
        model = FakeDecisionModel(
            route="evidence_qa",
            history_dependency="independent",
            standalone_query="Why does scaled dot-product attention divide by sqrt(d)?",
        )

        decision = analyze_turn("why does it do that?", conversation(), model=model)

        self.assertEqual(decision.history_dependency, "dependent")

    def test_a_transform_falls_back_when_there_is_nothing_to_transform(self) -> None:
        state = new_video_conversation_state(video_id=uuid4())
        state.messages.append(VideoMessage(role="user", content="shorter please"))
        model = FakeDecisionModel(route="prior_answer_transform")

        decision = analyze_turn("shorter please", state, model=model)

        self.assertEqual(decision.route, "evidence_qa")


class RewritingContextTests(unittest.TestCase):
    def test_the_router_sees_the_prior_answer_and_its_evidence(self) -> None:
        state = conversation()
        state.previous_evidence = [evidence(1), evidence(2, start_ms=300_000)]
        state.previous_citations = [
            VideoCitationRef(
                marker="[S1]", evidence_rank=1, modality="transcript", start_ms=60_000
            )
        ]
        model = FakeDecisionModel(standalone_query="rewritten")

        analyze_turn("what about the other one?", state, model=model)

        payload = model.payloads[0]
        self.assertEqual(payload["current_message"], "what about the other one?")
        self.assertIsNotNone(payload["previous_answer"])
        self.assertEqual(len(payload["previous_evidence"]), 2)
        self.assertEqual(payload["previous_evidence"][0]["marker"], "[S1]")

    def test_the_answerer_sees_the_same_turns_the_router_did(self) -> None:
        state = conversation()
        for index in range(4):
            state.messages.extend(
                [
                    VideoMessage(role="user", content=f"question {index}"),
                    VideoMessage(role="assistant", content=f"answer {index}"),
                ]
            )

        from video.prompts import conversation_context

        rendered = conversation_context(state.recent_messages(turns=3))
        messages = build_answer_messages(
            question="and after that?",
            evidence=[evidence()],
            video_title="Attention lecture",
            conversation_context=rendered,
        )

        system = messages[0]["content"]
        # Three turns, not two: the router rewrites against three, and an
        # answerer shown less can contradict the query it was handed.
        self.assertIn("question 3", system)
        self.assertIn("question 1", system)
        self.assertNotIn("question 0", system)
        self.assertIn("never evidence", system)


class TurnRecordingTests(unittest.TestCase):
    def _result(self, **overrides) -> VideoTurnResult:
        defaults = dict(
            question="What is attention?",
            answer="It weights values [S1].",
            route="evidence_qa",
            history_dependency="independent",
            evidence=[evidence()],
            citations=[
                VideoCitationRef(
                    marker="[S1]",
                    evidence_rank=1,
                    modality="transcript",
                    start_ms=60_000,
                )
            ],
            outcome="answer",
        )
        defaults.update(overrides)
        return VideoTurnResult(**defaults)

    def test_an_answer_becomes_what_the_next_turn_may_refer_to(self) -> None:
        state = record_turn(
            new_video_conversation_state(video_id=uuid4()),
            "What is attention?",
            self._result(),
        )

        self.assertEqual(state.previous_answer, "It weights values [S1].")
        self.assertEqual(len(state.previous_evidence), 1)
        self.assertIsNone(state.pending_clarification)
        self.assertEqual(state.previous_route, "evidence_qa")

    def test_a_clarification_keeps_the_message_it_could_not_route(self) -> None:
        state = record_turn(
            new_video_conversation_state(video_id=uuid4()),
            "that one",
            self._result(route="clarify", outcome="clarify", answer="Which one?"),
        )

        self.assertEqual(state.pending_clarification, "that one")
        # A clarification is not an answer, so it must not become one a later
        # turn can reshape.
        self.assertIsNone(state.previous_answer)

    def test_a_summary_is_available_to_reshape_afterwards(self) -> None:
        state = record_turn(
            new_video_conversation_state(video_id=uuid4()),
            "summarize this lecture",
            self._result(
                route="lecture_summary", answer="A long summary [S1].",
            ),
        )

        self.assertEqual(state.previous_route, "lecture_summary")
        self.assertEqual(state.previous_answer, "A long summary [S1].")
        self.assertEqual(len(state.previous_evidence), 1)

    def test_a_clarification_is_cleared_once_the_next_turn_answers(self) -> None:
        state = VideoConversationState(
            conversation_id=str(uuid4()),
            video_id=str(uuid4()),
            pending_clarification="that one",
        )

        state = record_turn(state, "the diagram at 12:00", self._result())

        self.assertIsNone(state.pending_clarification)


if __name__ == "__main__":
    unittest.main()
