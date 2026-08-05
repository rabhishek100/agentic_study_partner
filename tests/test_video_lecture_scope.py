"""Whole-lecture requests: routing, complete-transcript loading, and answers."""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.analyze import analyze_turn, lecture_scope_route
from video.answers import VideoAnswerDependencies
from video.conversation import execute_video_turn, new_video_conversation_state
from video.conversation_store import cost_ceiling
from video.contracts import VideoEvidenceRef, VideoMessage
from video.lecture import (
    LectureScope,
    NoTranscriptError,
    inventory_topics,
    load_chapters,
    load_lecture_scope,
    summarize_lecture,
)
from video.models import MAXIMUM_LECTURE_COST_USD, MAXIMUM_TURN_COST_USD


class FakeSummaryModel:
    """Records what it was asked and answers with markers it was given."""

    def __init__(self, *replies: str, cost: float = 0.01) -> None:
        self.replies = list(replies)
        self.cost = cost
        self.calls: list[list[dict]] = []

    def invoke(self, messages, config=None):
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else "A summary [S1]."

        class Response:
            content = reply
            response_metadata = {"cost": self.cost}

        return Response()


class LectureRoutingTests(unittest.TestCase):
    def test_matches_whole_lecture_requests(self) -> None:
        for question in (
            "summarize this video",
            "Summarize this lecture",
            "give me a brief summary",
            "tldr",
            "recap the lecture",
        ):
            with self.subTest(question=question):
                matched = lecture_scope_route(question)
                self.assertIsNotNone(matched)
                self.assertEqual(matched[0], "lecture_summary")

        for question in (
            "list the topics discussed in this video",
            "what topics are covered?",
            "what are the main topics",
            "outline",
            "what does this lecture cover",
        ):
            with self.subTest(question=question):
                matched = lecture_scope_route(question)
                self.assertIsNotNone(matched)
                self.assertEqual(matched[0], "topic_inventory")

    def test_leaves_questions_about_one_topic_to_retrieval(self) -> None:
        for question in (
            # A summary verb narrowed to one topic is still a question.
            "summarize what he said about attention",
            "what did he draw on the board?",
            "explain self-attention",
            # Asks about one of the topics, not for the list of them.
            "What is the second topic about?",
            "what is the chapter on transformers about",
        ):
            with self.subTest(question=question):
                self.assertIsNone(lecture_scope_route(question))

    def test_routes_without_calling_the_control_model(self) -> None:
        state = new_video_conversation_state(video_id=uuid4())
        # History present, so the control model would ordinarily be consulted.
        state.messages.append(
            VideoMessage(role="user", content="earlier question")
        )

        def explode(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("the control model was called")

        decision = analyze_turn("summarize this lecture", state, model=explode)

        self.assertEqual(decision.route, "lecture_summary")
        self.assertEqual(decision.history_dependency, "independent")


class LectureCostCeilingTests(unittest.TestCase):
    def test_a_whole_lecture_route_is_allowed_to_cost_more(self) -> None:
        self.assertEqual(cost_ceiling("evidence_qa"), MAXIMUM_TURN_COST_USD)
        self.assertEqual(cost_ceiling("lecture_summary"), MAXIMUM_LECTURE_COST_USD)
        self.assertEqual(cost_ceiling("topic_inventory"), MAXIMUM_LECTURE_COST_USD)
        self.assertGreater(MAXIMUM_LECTURE_COST_USD, MAXIMUM_TURN_COST_USD)


class LectureScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-lecture.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def test_loads_the_complete_transcript_in_time_order(self) -> None:
        with connection(self.database_url, readonly=True) as database:
            scope = load_lecture_scope(
                database, owner_id=self.owner, video_id=self.video.video_id
            )

        self.assertGreater(len(scope.windows), 0)
        self.assertEqual(
            [window.rank for window in scope.windows],
            list(range(1, len(scope.windows) + 1)),
        )
        starts = [window.start_ms for window in scope.windows]
        self.assertEqual(starts, sorted(starts))
        for window in scope.windows:
            # The inspector must not claim these were ranked and chosen.
            self.assertEqual(window.retrieval_method, "complete_transcript")
            self.assertEqual(window.modality, "transcript")

    def test_abstains_when_there_is_no_transcript(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                """
                delete from video.evidence_units
                where owner_id = %s and video_id = %s and modality = 'transcript'
                """,
                (self.owner, self.video.video_id),
            )
            with self.assertRaises(NoTranscriptError):
                load_lecture_scope(
                    database, owner_id=self.owner, video_id=self.video.video_id
                )

    def test_a_summary_turn_reads_every_window_and_cites_them(self) -> None:
        model = FakeSummaryModel("The lecturer opens with attention [S1].")
        with connection(self.database_url) as database:
            result, _ = execute_video_turn(
                database,
                "summarize this lecture",
                new_video_conversation_state(video_id=self.video.video_id),
                owner_id=self.owner,
                video_id=self.video.video_id,
                video_title="Attention lecture",
                dependencies=VideoAnswerDependencies(model=model),
            )

        self.assertEqual(result.route, "lecture_summary")
        self.assertEqual(result.outcome, "answer")
        self.assertEqual([c.marker for c in result.citations], ["[S1]"])
        # Retrieval never ran, so claiming a pass would be a lie.
        self.assertEqual(result.retrieval_attempts, 0)
        self.assertIn("complete transcript", result.sufficiency_reason)
        # Only cited windows are carried; all of them were supplied.
        self.assertEqual([item.rank for item in result.evidence], [1])
        prompt = model.calls[0][1]["content"]
        self.assertIn("[S1]", prompt)

    def test_an_inventory_turn_uses_published_chapters_without_a_model(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                """
                insert into video.chapters (
                    owner_id, video_id, video_source_id, chapter_index,
                    chapter_kind, title, start_ms, end_ms
                ) values
                    (%s, %s, %s, 0, 'youtube', 'Why attention', 0, 120000),
                    (%s, %s, %s, 1, 'youtube', 'Scaled dot product',
                     120000, 240000)
                """,
                (
                    self.owner,
                    self.video.video_id,
                    self.video.source_id,
                    self.owner,
                    self.video.video_id,
                    self.video.source_id,
                ),
            )

            def explode(*args, **kwargs):  # pragma: no cover - must not run
                raise AssertionError("a model was called for a chapter list")

            result, _ = execute_video_turn(
                database,
                "list the topics covered in this lecture",
                new_video_conversation_state(video_id=self.video.video_id),
                owner_id=self.owner,
                video_id=self.video.video_id,
                video_title="Attention lecture",
                dependencies=VideoAnswerDependencies(model=explode),
            )

        self.assertEqual(result.route, "topic_inventory")
        self.assertEqual(result.cost_usd, 0.0)
        self.assertIn("Why attention", result.answer)
        self.assertIn("Scaled dot product", result.answer)
        # Each row cites a real transcript window, so its timestamp is
        # clickable rather than being metadata the reader cannot verify.
        self.assertTrue(result.citations)

    def test_an_inventory_falls_back_to_the_transcript_without_chapters(self) -> None:
        model = FakeSummaryModel("- Attention [S1]")
        with connection(self.database_url) as database:
            chapters = load_chapters(
                database, owner_id=self.owner, video_id=self.video.video_id
            )
            self.assertEqual(chapters, [])
            scope = load_lecture_scope(
                database, owner_id=self.owner, video_id=self.video.video_id
            )

        draft = inventory_topics(
            question="what topics are covered",
            scope=scope,
            video_title="Attention lecture",
            chapters=[],
            dependencies=VideoAnswerDependencies(model=model),
        )

        self.assertEqual(len(model.calls), 1)
        self.assertEqual([c.marker for c in draft.citations], ["[S1]"])

class LongLectureTests(unittest.TestCase):
    """A lecture too long for one call is mapped and reduced, never truncated."""

    def _scope(self, windows: int) -> LectureScope:
        return LectureScope(
            version_id=uuid4(),
            duration_ms=windows * 60_000,
            windows=[
                VideoEvidenceRef(
                    rank=index,
                    evidence_id=f"{index:064d}",
                    modality="transcript",
                    excerpt="word " * 400,
                    retrieval_method="complete_transcript",
                    score=1.0,
                    start_ms=(index - 1) * 60_000,
                    end_ms=index * 60_000,
                )
                for index in range(1, windows + 1)
            ],
        )

    def test_every_window_reaches_a_call_and_the_parts_are_combined(self) -> None:
        scope = self._scope(6)
        import video.lecture as module

        original = module.BATCH_CHARACTERS
        # Two windows per batch: three mapped calls, then one reduce.
        module.BATCH_CHARACTERS = len(scope.windows[0].excerpt) * 2
        try:
            model = FakeSummaryModel(
                "Part one [S1].",
                "Part two [S3].",
                "Part three [S5].",
                "Combined [S1] [S3] [S5].",
            )
            draft = summarize_lecture(
                question="summarize this lecture",
                scope=scope,
                video_title="A long lecture",
                chapters=[],
                dependencies=VideoAnswerDependencies(model=model),
            )
        finally:
            module.BATCH_CHARACTERS = original

        self.assertEqual(len(model.calls), 4)
        self.assertEqual(draft.answer, "Combined [S1] [S3] [S5].")
        # Markers survive the reduce, so they still point at real windows.
        self.assertEqual(
            [citation.marker for citation in draft.citations],
            ["[S1]", "[S3]", "[S5]"],
        )
        # Cost accumulates across every call rather than reporting the last.
        self.assertAlmostEqual(draft.cost_usd, 0.04, places=6)

    def test_ranks_stay_global_so_a_later_batch_cites_correctly(self) -> None:
        scope = self._scope(4)
        import video.lecture as module

        original = module.BATCH_CHARACTERS
        module.BATCH_CHARACTERS = len(scope.windows[0].excerpt)
        try:
            model = FakeSummaryModel(
                "a", "b", "c", "d", "Combined [S4]."
            )
            summarize_lecture(
                question="summarize this lecture",
                scope=scope,
                video_title="A long lecture",
                chapters=[],
                dependencies=VideoAnswerDependencies(model=model),
            )
        finally:
            module.BATCH_CHARACTERS = original

        # The final batch must present its window as [S4], not as [S1] of a
        # batch that happens to start there.
        self.assertIn("[S4]", model.calls[3][1]["content"])


if __name__ == "__main__":
    unittest.main()
