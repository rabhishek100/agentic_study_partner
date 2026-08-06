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
    coverage_units,
    evaluate_coverage,
    inventory_topics,
    load_chapters,
    load_lecture_scope,
    restrict_chapters,
    restrict_scope,
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
                self.assertIsNone(matched[2])

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

    def test_a_request_for_part_of_the_lecture_still_summarizes(self) -> None:
        """"The first half" used to fall through to top-k retrieval.

        Fifty minutes of lecture answered from eight passages reads exactly
        like a summary and is not one, which is the failure the whole-lecture
        routes exist to prevent. Asking for half of it should not bring it
        back.
        """

        duration = 6_000_000
        expected = {
            "summarize the first half": (0, 3_000_000),
            "give me a recap of the second half": (3_000_000, 6_000_000),
            "walk me through the middle third": (2_000_000, 4_000_000),
            "summarize the final quarter": (4_500_000, 6_000_000),
            "summarize the first 15 minutes": (0, 900_000),
            "summarize the last 20 minutes": (4_800_000, 6_000_000),
            "summarize from 20:00 to 40:00": (1_200_000, 2_400_000),
        }
        for question, span in expected.items():
            with self.subTest(question=question):
                matched = lecture_scope_route(question)
                self.assertIsNotNone(matched)
                route, _, scope = matched
                self.assertEqual(route, "lecture_summary")
                self.assertIsNotNone(scope)
                self.assertEqual(scope.resolve(duration), span)

    def test_a_whole_lecture_summary_carries_no_stretch(self) -> None:
        route, _, scope = lecture_scope_route("Summarize this lecture")
        self.assertEqual(route, "lecture_summary")
        self.assertIsNone(scope)

    def test_a_time_phrase_alone_is_not_a_summary_request(self) -> None:
        for question in (
            # Narrowed to one topic: a question that mentions a stretch.
            "summarize what he said about attention in the first half",
            # No summary verb at all.
            "what happened in the first half?",
            # Not a stretch anyone can name.
            "summarize the middle half",
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


class RestrictedScopeTests(unittest.TestCase):
    """Summarizing part of a lecture from exactly that part of it."""

    def scope(self) -> LectureScope:
        return LectureScope(
            version_id=uuid4(),
            windows=[
                VideoEvidenceRef(
                    rank=rank,
                    evidence_id=f"w{rank}",
                    modality="transcript",
                    # Long enough to count as substantive; a window under
                    # SUBSTANTIVE_WINDOW_CHARACTERS is optional coverage.
                    excerpt=f"window {rank} content " * 20,
                    retrieval_method="complete_transcript",
                    score=1.0,
                    start_ms=(rank - 1) * 60_000,
                    end_ms=rank * 60_000,
                )
                for rank in range(1, 11)
            ],
            duration_ms=600_000,
        )

    def test_only_the_windows_the_stretch_touches_survive(self) -> None:
        narrowed = restrict_scope(self.scope(), start_ms=300_000, end_ms=600_000)
        self.assertEqual(
            [window.rank for window in narrowed.windows], [6, 7, 8, 9, 10]
        )

    def test_ranks_keep_their_meaning_across_the_whole_lecture(self) -> None:
        """A marker must mean the same moment in every turn.

        Renumbering the narrowed windows from one would make [S1] the opening
        of the lecture in one turn and its midpoint in the next, inside the
        same conversation and the same reference panel.
        """

        narrowed = restrict_scope(self.scope(), start_ms=300_000, end_ms=600_000)
        self.assertEqual(narrowed.windows[0].rank, 6)
        self.assertEqual(narrowed.windows[0].start_ms, 300_000)
        # The recording did not get shorter because the request did.
        self.assertEqual(narrowed.duration_ms, 600_000)

    def test_a_partly_overlapping_window_is_kept(self) -> None:
        # Windows are cut on transcript boundaries and a half is not, so the
        # window straddling the midpoint belongs to both halves.
        narrowed = restrict_scope(self.scope(), start_ms=330_000, end_ms=600_000)
        self.assertEqual(narrowed.windows[0].rank, 6)

    def test_coverage_is_checked_over_the_stretch_that_was_asked_for(self) -> None:
        narrowed = restrict_scope(self.scope(), start_ms=300_000, end_ms=600_000)
        units = coverage_units(narrowed, [])
        self.assertEqual(len(units), 5)
        # Citing the first half does not cover the second.
        self.assertFalse(evaluate_coverage("Opening [S1][S2].", units).complete)
        self.assertTrue(
            evaluate_coverage(
                "A [S6]. B [S7]. C [S8]. D [S9]. E [S10].", units
            ).complete
        )

    def test_chapters_outside_the_stretch_are_not_offered_as_an_outline(
        self,
    ) -> None:
        chapters = [
            {"chapter_index": 0, "title": "Opening", "start_ms": 0, "end_ms": 200_000},
            {
                "chapter_index": 1,
                "title": "Middle",
                "start_ms": 200_000,
                "end_ms": 400_000,
            },
            {
                "chapter_index": 2,
                "title": "Close",
                "start_ms": 400_000,
                "end_ms": 600_000,
            },
        ]
        kept = restrict_chapters(chapters, start_ms=300_000, end_ms=600_000)
        self.assertEqual([chapter["title"] for chapter in kept], ["Middle", "Close"])

    def test_the_prompt_is_told_it_is_summarizing_a_stretch(self) -> None:
        """Otherwise "cover the whole lecture" licenses inventing the rest."""

        narrowed = restrict_scope(self.scope(), start_ms=300_000, end_ms=600_000)
        model = FakeSummaryModel("Second half [S6][S7][S8][S9][S10].")
        summarize_lecture(
            question="summarize the second half",
            scope=narrowed,
            video_title="Lecture",
            chapters=[],
            dependencies=VideoAnswerDependencies(model=model),
            stretch="the second half",
        )

        system = model.calls[0][0]["content"]
        self.assertIn("the second half", system)
        self.assertIn("nothing about the parts of the recording outside it", system)

    def test_a_whole_lecture_summary_is_told_nothing_about_stretches(self) -> None:
        model = FakeSummaryModel("Everything [S1].")
        summarize_lecture(
            question="summarize this lecture",
            scope=self.scope(),
            video_title="Lecture",
            chapters=[],
            dependencies=VideoAnswerDependencies(model=model),
        )
        self.assertNotIn(
            "nothing about the parts of the recording outside it",
            model.calls[0][0]["content"],
        )


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
                "Part one [S1] [S2].",
                "Part two [S3] [S4].",
                "Part three [S5] [S6].",
                "Combined [S1] [S2] [S3] [S4] [S5] [S6].",
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

        # Three mapped stretches plus one reduce. No repair call, because the
        # combined draft already cited every window.
        self.assertEqual(len(model.calls), 4)
        self.assertEqual(draft.answer, "Combined [S1] [S2] [S3] [S4] [S5] [S6].")
        # Markers survive the reduce, so they still point at real windows.
        self.assertEqual(
            [citation.marker for citation in draft.citations],
            ["[S1]", "[S2]", "[S3]", "[S4]", "[S5]", "[S6]"],
        )
        # Cost accumulates across every call rather than reporting the last.
        self.assertAlmostEqual(draft.cost_usd, 0.04, places=6)
        self.assertEqual(draft.warnings, ())

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


class SummaryCoverageTests(unittest.TestCase):
    """A summary is checked against the lecture, not trusted to cover it."""

    def _scope(self, windows: int, *, characters: int = 2_000) -> LectureScope:
        return LectureScope(
            version_id=uuid4(),
            duration_ms=windows * 60_000,
            windows=[
                VideoEvidenceRef(
                    rank=index,
                    evidence_id=f"{index:064d}",
                    modality="transcript",
                    excerpt="x" * characters,
                    retrieval_method="complete_transcript",
                    score=1.0,
                    start_ms=(index - 1) * 60_000,
                    end_ms=index * 60_000,
                )
                for index in range(1, windows + 1)
            ],
        )

    def _chapters(self) -> list[dict]:
        return [
            {"chapter_index": 0, "title": "Opening", "start_ms": 0, "end_ms": 120_000},
            {
                "chapter_index": 1,
                "title": "The main result",
                "start_ms": 120_000,
                "end_ms": 240_000,
            },
        ]

    def test_published_chapters_are_the_units_a_summary_must_cover(self) -> None:
        units = coverage_units(self._scope(4), self._chapters())

        self.assertEqual([unit.key for unit in units], ["chapter:0", "chapter:1"])
        self.assertEqual(units[0].window_ranks, (1, 2))
        self.assertEqual(units[1].window_ranks, (3, 4))
        self.assertIn("The main result", units[1].label)

    def test_windows_are_the_units_when_no_chapters_were_published(self) -> None:
        units = coverage_units(self._scope(3), [])

        self.assertEqual(len(units), 3)
        self.assertTrue(all(unit.required for unit in units))

    def test_a_window_too_thin_to_carry_content_is_optional(self) -> None:
        scope = self._scope(2, characters=10)

        units = coverage_units(scope, [])

        # Requiring a citation for near-silence would force the padding the
        # summary prompt forbids.
        self.assertFalse(any(unit.required for unit in units))

    def test_a_chapter_the_transcript_never_reaches_is_not_required(self) -> None:
        # One window of lecture, but a chapter list running to four minutes.
        units = coverage_units(self._scope(1), self._chapters())

        self.assertEqual([unit.key for unit in units], ["chapter:0"])

    def test_coverage_is_measured_from_what_was_cited(self) -> None:
        scope = self._scope(4)
        units = coverage_units(scope, self._chapters())

        complete = evaluate_coverage("Opening [S1]. Result [S3].", units)
        self.assertTrue(complete.complete)
        self.assertEqual(complete.describe(), "2 of 2 required stretches cited")

        partial = evaluate_coverage("Only the opening [S2].", units)
        self.assertFalse(partial.complete)
        self.assertEqual(
            [unit.key for unit in partial.missing_required], ["chapter:1"]
        )

    def test_a_draft_that_skips_a_stretch_is_repaired_not_shipped(self) -> None:
        scope = self._scope(4)
        model = FakeSummaryModel(
            "The lecturer opens with definitions [S1].",
            "He then proves the main result [S3].",
        )

        draft = summarize_lecture(
            question="summarize this lecture",
            scope=scope,
            video_title="A lecture",
            chapters=self._chapters(),
            dependencies=VideoAnswerDependencies(model=model),
        )

        self.assertEqual(len(model.calls), 2)
        # The repair is an addendum, so the first draft survives intact.
        self.assertIn("The lecturer opens with definitions [S1]", draft.answer)
        self.assertIn("proves the main result [S3]", draft.answer)
        self.assertEqual(draft.coverage, "2 of 2 required stretches cited")
        self.assertEqual(draft.warnings, ())
        # The repair was asked only about what was missing.
        repair = model.calls[1][1]["content"]
        self.assertIn("The main result", repair)
        self.assertNotIn("Opening", repair)

    def test_a_stretch_still_missing_after_repair_is_declared(self) -> None:
        scope = self._scope(4)
        # Both the draft and the repair ignore the second chapter.
        model = FakeSummaryModel("Opening only [S1].", "Still the opening [S2].")

        draft = summarize_lecture(
            question="summarize this lecture",
            scope=scope,
            video_title="A lecture",
            chapters=self._chapters(),
            dependencies=VideoAnswerDependencies(model=model),
        )

        self.assertEqual(draft.coverage, "1 of 2 required stretches cited")
        self.assertEqual(len(draft.warnings), 1)
        self.assertIn("The main result", draft.warnings[0])
        # Repair is attempted exactly once; a second pass has the same
        # evidence and mostly spends money to reword.
        self.assertEqual(len(model.calls), 2)

    def test_the_required_stretches_are_named_in_the_prompt(self) -> None:
        scope = self._scope(4)
        model = FakeSummaryModel("Everything [S1] [S3].")

        summarize_lecture(
            question="summarize this lecture",
            scope=scope,
            video_title="A lecture",
            chapters=self._chapters(),
            dependencies=VideoAnswerDependencies(model=model),
        )

        system = model.calls[0][0]["content"]
        self.assertIn("Required coverage:", system)
        self.assertIn("Opening", system)
        self.assertIn("The main result", system)
        self.assertIn("Complete coverage is mandatory", system)

    def test_a_summary_is_not_streamed_before_it_has_been_checked(self) -> None:
        scope = self._scope(2)
        model = FakeSummaryModel("Everything [S1] [S2].")
        streamed: list[tuple[str, str]] = []

        draft = summarize_lecture(
            question="summarize this lecture",
            scope=scope,
            video_title="A lecture",
            chapters=[],
            dependencies=VideoAnswerDependencies(model=model),
            token_callback=lambda kind, text: streamed.append((kind, text)),
        )

        # One delivery of the validated text, not a token stream of a draft
        # that a coverage addendum might still be appended to.
        self.assertEqual(streamed, [("token", draft.answer)])
