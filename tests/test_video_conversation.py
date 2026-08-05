"""Video turns stay grounded, cited, resumable, and honest about gaps."""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.analyze import ModelDecision
from video.answers import VideoAnswerDependencies
from video.contracts import VideoConversationState
from video.conversation import (
    evaluate_sufficiency,
    execute_video_turn,
    new_video_conversation_state,
)
from video.conversation_store import (
    append_turn,
    create_conversation,
    list_conversations,
    load_conversation,
    load_turns,
)


class FakeReply:
    def __init__(self, content: str, cost: float | None = None) -> None:
        self.content = content
        self.response_metadata = {"cost": cost} if cost is not None else {}


class FakeAnswerModel:
    """A chat model that records what it was asked and replies in order."""

    def __init__(self, *replies: str, cost: float | None = None) -> None:
        self.replies = list(replies)
        self.cost = cost
        self.requests: list = []

    def _next(self, messages) -> FakeReply:
        self.requests.append(messages)
        content = self.replies.pop(0) if self.replies else "No reply configured."
        return FakeReply(content, self.cost)

    def invoke(self, messages) -> FakeReply:
        return self._next(messages)

    def stream(self, messages):
        reply = self._next(messages)
        for piece in reply.content.split(" "):
            yield FakeReply(piece + " ")


class FakeAnalysisModel:
    def __init__(self, decision: ModelDecision) -> None:
        self.decision = decision
        self.payloads: list = []

    def invoke(self, messages) -> ModelDecision:
        self.payloads.append(messages)
        return self.decision


class VideoConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-conversation.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def _turn(self, database, video, question, *, model, state=None, analysis=None):
        return execute_video_turn(
            database,
            question,
            state or new_video_conversation_state(video_id=video.video_id),
            owner_id=self.owner,
            video_id=video.video_id,
            video_title=video.title,
            dependencies=VideoAnswerDependencies(model=model),
            analysis_model=analysis,
        )

    def test_answers_from_evidence_with_clickable_locators(self) -> None:
        model = FakeAnswerModel(
            "The lecturer draws the attention diagram [S1] and explains it [S2].",
            cost=0.0042,
        )
        with connection(self.database_url) as database:
            video = publish_video_with_evidence(database, owner_id=self.owner)
            result, state = self._turn(
                database, video, "what does the softmax diagram show?", model=model
            )

        self.assertEqual(result.outcome, "answer")
        self.assertEqual(result.route, "evidence_qa")
        self.assertEqual(result.ingestion_version_id, str(video.version_id))
        self.assertEqual(result.cost_usd, 0.0042)
        self.assertTrue(result.evidence)
        self.assertEqual([item.rank for item in result.evidence], list(range(1, len(result.evidence) + 1)))
        self.assertEqual([c.marker for c in result.citations], ["[S1]", "[S2]"])
        for citation in result.citations:
            if citation.modality == "resource_page":
                self.assertIsNotNone(citation.page_number)
            else:
                self.assertIsNotNone(citation.start_ms)
        self.assertTrue(result.visual_cards)
        self.assertLessEqual(len(result.visual_cards), 4)
        # The prompt carries the evidence markers the answer is asked to cite.
        prompt = model.requests[0][1]["content"][0]["text"]
        self.assertIn("[S1]", prompt)
        self.assertIn("Frame at", prompt)
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.previous_answer, result.answer)

    def test_admits_missing_evidence_instead_of_answering_anyway(self) -> None:
        model = FakeAnswerModel(
            "INSUFFICIENT_EVIDENCE: the lecture never gives that number."
        )
        with connection(self.database_url) as database:
            video = publish_video_with_evidence(database, owner_id=self.owner)
            result, _ = self._turn(
                database,
                video,
                "how many attention heads does the model use?",
                model=model,
            )

        self.assertEqual(result.outcome, "abstain")
        self.assertTrue(result.answer.startswith("Insufficient evidence:"))
        self.assertEqual(result.citations, [])

    def test_a_visual_question_broadens_before_giving_up(self) -> None:
        model = FakeAnswerModel("He draws the attention diagram [S1].")
        with connection(self.database_url) as database:
            video = publish_video_with_evidence(database, owner_id=self.owner)
            result, _ = self._turn(
                database, video, "what does he draw on the board?", model=model
            )

        # The words match only speech, and the frames sit outside the default
        # timeline window, so the first attempt is deliberately insufficient.
        self.assertEqual(result.retrieval_attempts, 2)
        self.assertTrue(any(item.is_visual for item in result.evidence))
        self.assertEqual(result.outcome, "answer")

    def test_a_follow_up_is_rewritten_before_it_is_retrieved(self) -> None:
        analysis = FakeAnalysisModel(
            ModelDecision(
                route="evidence_qa",
                history_dependency="dependent",
                standalone_query="what does the scaled dot-product attention diagram show?",
                reason="Resolved 'that diagram' from the previous answer.",
            )
        )
        model = FakeAnswerModel(
            "The diagram shows the attention computation [S1].",
            "It weights values by the softmax scores [S1].",
        )
        with connection(self.database_url) as database:
            video = publish_video_with_evidence(database, owner_id=self.owner)
            _, state = self._turn(
                database, video, "what is on screen at two minutes?", model=model
            )
            result, state = self._turn(
                database,
                video,
                "what did that diagram show?",
                model=model,
                state=state,
                analysis=analysis,
            )

        self.assertEqual(len(analysis.payloads), 1)
        self.assertEqual(
            result.standalone_query,
            "what does the scaled dot-product attention diagram show?",
        )
        self.assertEqual(result.history_dependency, "dependent")
        self.assertTrue(result.evidence)
        self.assertEqual(len(state.messages), 4)

    def test_routing_failure_still_answers_the_question_as_asked(self) -> None:
        class BrokenAnalysis:
            def invoke(self, messages):
                raise RuntimeError("control model unavailable")

        model = FakeAnswerModel("The board shows the attention diagram [S1].")
        with connection(self.database_url) as database:
            video = publish_video_with_evidence(database, owner_id=self.owner)
            _, state = self._turn(
                database, video, "what is on the board?", model=model
            )
            result, _ = self._turn(
                database,
                video,
                "and what changes after that?",
                model=model,
                state=state,
                analysis=BrokenAnalysis(),
            )

        self.assertEqual(result.route, "evidence_qa")
        self.assertIn("Routing unavailable", result.routing_reason)

    def test_turns_persist_with_their_pinned_version_and_resume(self) -> None:
        model = FakeAnswerModel("The lecturer explains attention [S1].", cost=0.001)
        with connection(self.database_url) as database:
            video = publish_video_with_evidence(database, owner_id=self.owner)
            conversation = create_conversation(
                database,
                owner_id=self.owner,
                video_id=video.video_id,
                title="what does the diagram show?",
            )
            state = new_video_conversation_state(
                video_id=video.video_id, conversation_id=conversation["id"]
            )
            result, updated = self._turn(
                database, video, "what does the diagram show?", model=model, state=state
            )
            index = append_turn(
                database,
                conversation["id"],
                owner_id=self.owner,
                video_id=video.video_id,
                ingestion_version_id=result.ingestion_version_id,
                question=result.question,
                rewritten_query=result.standalone_query or result.question,
                answer=result.answer,
                result=result.model_dump(mode="json"),
                state=updated.model_dump(mode="json"),
                cost_usd=result.cost_usd,
                trace_id=result.trace_id,
            )
            stored = load_conversation(
                database, conversation["id"], owner_id=self.owner
            )
            turns = load_turns(database, conversation["id"], owner_id=self.owner)
            listed = list_conversations(
                database, owner_id=self.owner, video_id=video.video_id
            )
            resumed = VideoConversationState.model_validate(stored["state_json"])

        self.assertEqual(index, 0)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["status"], "complete")
        self.assertEqual(float(turns[0]["actual_cost_usd"]), 0.001)
        self.assertEqual(turns[0]["result_json"]["outcome"], "answer")
        self.assertEqual(listed[0]["turn_count"], 1)
        self.assertEqual(listed[0]["video_title"], video.title)
        self.assertEqual(len(resumed.messages), 2)
        self.assertEqual(resumed.previous_answer, result.answer)

    def test_sufficiency_names_what_is_missing(self) -> None:
        sufficient, reason = evaluate_sufficiency("what is attention?", [])
        self.assertFalse(sufficient)
        self.assertIn("No evidence", reason)


if __name__ == "__main__":
    unittest.main()
