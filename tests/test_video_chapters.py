"""An outline for a lecture whose source shipped none.

The signal is what the lecturer put on screen: a slide title stays while its
body changes, and a new title is a new topic. Everything here is about the two
ways that reading goes wrong — a misread frame that looks like a topic change,
and a slide that is not a topic — plus the one rule that must never bend, which
is that a derived outline never overwrites one the source published.
"""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.chapters import (
    MINIMUM_CHAPTER_MS,
    DerivedChapter,
    derive_chapters,
)
from video.repository import replace_derived_chapters


MINUTE = 60_000


def frames(*pairs):
    """(minutes, visible text) as the observation rows the deriver reads."""

    return [
        {"timestamp_ms": int(minute * MINUTE), "visible_text": text}
        for minute, text in pairs
    ]


class DeriveChaptersTests(unittest.TestCase):
    def test_a_run_of_one_slide_title_becomes_one_chapter(self) -> None:
        chapters = derive_chapters(
            frames(
                (0, "Tokenization A cute teddy bear is reading"),
                (2, "Tokenization A cute teddy bear is reading word level"),
                (4, "Tokenization A cute teddy bear is reading sub-word"),
                (6, "Token representations Motivation Naive one-hot encoding"),
                (8, "Token representations Motivation cosine similarity"),
                (10, "Token representations Learned embeddings"),
            ),
            duration_ms=12 * MINUTE,
        )

        self.assertEqual(
            [chapter.title for chapter in chapters],
            # "A cute teddy bear is reading" is the slide's body, not its
            # title: a capitalised article mid-line starts a sentence.
            ["Tokenization", "Token representations"],
        )
        self.assertEqual(chapters[0].start_ms, 0)

    def test_chapters_tile_the_lecture_with_no_gaps(self) -> None:
        """Coverage over the chapters has to be coverage over all of it.

        A summary is checked against them, so a moment belonging to no chapter
        is a moment nothing requires the summary to mention.
        """

        duration = 20 * MINUTE
        chapters = derive_chapters(
            frames(
                (0, "Tokenization word level sub-word"),
                (3, "Tokenization word level character"),
                (7, "Recurrent neural networks hidden state"),
                (11, "Recurrent neural networks vanishing gradient"),
                (15, "Attention mechanism query key value"),
                (18, "Attention mechanism softmax weights"),
            ),
            duration_ms=duration,
        )

        self.assertGreater(len(chapters), 1)
        self.assertEqual(chapters[0].start_ms, 0)
        self.assertGreaterEqual(chapters[-1].end_ms, duration)
        for earlier, later in zip(chapters, chapters[1:]):
            self.assertEqual(earlier.end_ms, later.start_ms)

    def test_one_misread_frame_does_not_split_a_section(self) -> None:
        """A wide shot of the room is not a topic change.

        A boundary has to be confirmed by the frame after it, or a single
        unlucky angle produces a split indistinguishable from a real one.
        """

        chapters = derive_chapters(
            frames(
                (0, "Attention mechanism query key value"),
                (2, "Attention mechanism query key value weighted"),
                (4, "Lecturer standing beside the projector"),
                (6, "Attention mechanism softmax over the keys"),
                (8, "Attention mechanism scaled dot product"),
                (10, "Transformer architecture encoder decoder"),
                (12, "Transformer architecture positional encoding"),
            ),
            duration_ms=14 * MINUTE,
        )

        self.assertEqual(
            [chapter.title for chapter in chapters],
            ["Attention mechanism", "Transformer architecture"],
        )

    def test_a_frame_the_reader_could_not_read_extends_rather_than_splits(
        self,
    ) -> None:
        chapters = derive_chapters(
            frames(
                (0, "Word2vec Overview proxy task"),
                (2, "Projected text is too faint to read reliably"),
                (4, "Word2vec Overview CBOW and skip-gram"),
                (6, "Recurrent neural networks hidden state"),
                (8, "Recurrent neural networks context vector"),
            ),
            duration_ms=10 * MINUTE,
        )
        self.assertEqual(
            [chapter.title for chapter in chapters],
            ["Word2vec Overview", "Recurrent neural networks"],
        )

    def test_a_slide_too_brief_to_be_a_topic_is_folded_in(self) -> None:
        chapters = derive_chapters(
            frames(
                (0, "Tokenization overview word level"),
                (3, "Tokenization overview sub-word level"),
                (6, "Thank you for your attention"),
                (6.5, "Recurrent neural networks hidden state"),
                (9, "Recurrent neural networks vanishing gradient"),
            ),
            duration_ms=12 * MINUTE,
        )
        titles = [chapter.title for chapter in chapters]
        self.assertNotIn("Thank you for your attention", titles)
        self.assertEqual(
            titles, ["Tokenization overview", "Recurrent neural networks"]
        )

    def test_a_lecture_that_shows_nothing_readable_gets_no_outline(self) -> None:
        """Silence beats a confident fiction where a reader navigates."""

        self.assertEqual(derive_chapters([], duration_ms=MINUTE), ())
        self.assertEqual(
            derive_chapters(
                frames((0, "blurred"), (2, "indistinct"), (4, "unreadable")),
                duration_ms=6 * MINUTE,
            ),
            (),
        )

    def test_one_chapter_for_the_whole_lecture_is_not_a_segmentation(self) -> None:
        chapters = derive_chapters(
            frames(
                (0, "Attention mechanism query key value"),
                (5, "Attention mechanism softmax weights"),
                (10, "Attention mechanism scaled dot product"),
            ),
            duration_ms=15 * MINUTE,
        )
        self.assertEqual(chapters, ())

    def test_titles_stay_short_enough_to_scan(self) -> None:
        long_slide = " ".join(f"word{index}" for index in range(40))
        chapters = derive_chapters(
            frames(
                (0, long_slide),
                (3, long_slide),
                (7, "Recurrent neural networks hidden state"),
                (10, "Recurrent neural networks context vector"),
            ),
            duration_ms=14 * MINUTE,
        )
        self.assertLessEqual(len(chapters[0].title), 80)

    def test_every_chapter_is_long_enough_to_navigate_to(self) -> None:
        chapters = derive_chapters(
            frames(
                (0, "Tokenization word level"),
                (3, "Tokenization sub-word"),
                (6, "Word2vec proxy task"),
                (9, "Word2vec CBOW skip-gram"),
                (12, "Attention mechanism query key"),
                (15, "Attention mechanism value"),
            ),
            duration_ms=18 * MINUTE,
        )
        for chapter in chapters:
            with self.subTest(title=chapter.title):
                self.assertGreaterEqual(
                    chapter.end_ms - chapter.start_ms, MINIMUM_CHAPTER_MS
                )


class ReplaceDerivedChaptersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-chapters.test"),
            )
            self.video = publish_video_with_evidence(
                database, owner_id=self.owner
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def outline(self, database):
        return database.execute(
            """
            select chapter_index, chapter_kind, title, start_ms, end_ms
            from video.chapters where owner_id = %s and video_id = %s
            order by chapter_index
            """,
            (self.owner, self.video.video_id),
        ).fetchall()

    def derived(self, *titles):
        return [
            DerivedChapter(
                index=index,
                title=title,
                start_ms=index * 100_000,
                end_ms=(index + 1) * 100_000,
                frame_count=3,
            )
            for index, title in enumerate(titles)
        ]

    def test_a_derived_outline_is_written_and_marked_as_derived(self) -> None:
        with connection(self.database_url) as database:
            written = replace_derived_chapters(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                chapters=self.derived("Tokenization", "Attention"),
            )
            rows = self.outline(database)

        self.assertEqual(written, 2)
        self.assertEqual([row["title"] for row in rows], ["Tokenization", "Attention"])
        self.assertEqual({row["chapter_kind"] for row in rows}, {"derived"})

    def test_re_deriving_replaces_rather_than_duplicates(self) -> None:
        with connection(self.database_url) as database:
            replace_derived_chapters(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                chapters=self.derived("First", "Second", "Third"),
            )
            replace_derived_chapters(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                chapters=self.derived("Only", "Two"),
            )
            rows = self.outline(database)

        self.assertEqual([row["title"] for row in rows], ["Only", "Two"])

    def test_frame_selection_ignores_a_derived_outline(self) -> None:
        """The feedback loop that made a re-ingest re-pay for vision.

        Frame selection hashes the chapter list, because chapters tell it
        which stretches need a frame. A derived outline is computed *from* the
        frames it selects, so feeding it back makes the stage depend on its own
        output: the run after a derivation sees chapters that did not exist
        before, misses its cache, re-selects frames, and invalidates the visual
        analysis behind it — the single most expensive stage in the pipeline.

        Observed in production: writing 21 derived chapters caused the next
        re-ingest to re-run the vision model over all 258 frames.
        """

        with connection(self.database_url) as database:
            replace_derived_chapters(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                chapters=self.derived("Tokenization", "Attention"),
            )
            seen = database.execute(
                """
                select count(*) as count from video.chapters
                where owner_id = %s and video_id = %s
                  and chapter_kind <> 'derived'
                """,
                (self.owner, self.video.video_id),
            ).fetchone()["count"]
            total = database.execute(
                "select count(*) as count from video.chapters where video_id = %s",
                (self.video.video_id,),
            ).fetchone()["count"]

        # The stage reads the first number; the outline has the second.
        self.assertEqual(seen, 0)
        self.assertEqual(total, 2)

    def test_the_api_can_serve_a_derived_outline(self) -> None:
        """Widening the database without widening the contract breaks reading.

        `ChapterView.chapter_kind` was a Literal of the two kinds that existed.
        Deriving a third and writing it to the database left the detail
        endpoint unable to serialise its own rows — the video page 500s, and
        every test that never wrote a derived chapter still passes.
        """

        from api.videos import ChapterView

        with connection(self.database_url) as database:
            replace_derived_chapters(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                chapters=self.derived("Tokenization"),
            )
            rows = self.outline(database)

        view = ChapterView(**rows[0])
        self.assertEqual(view.chapter_kind, "derived")

    def test_an_outline_the_source_published_is_never_overwritten(self) -> None:
        """Deriving is what happens without a list, not a correction of one."""

        with connection(self.database_url) as database:
            source = database.execute(
                "select id from video.video_sources where video_id = %s and is_primary",
                (self.video.video_id,),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.chapters (
                    owner_id, video_id, video_source_id, chapter_index,
                    chapter_kind, title, start_ms, end_ms
                ) values (%s, %s, %s, 0, 'youtube', 'Published by the source',
                          0, 500000)
                """,
                (self.owner, self.video.video_id, source),
            )
            written = replace_derived_chapters(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                chapters=self.derived("Derived guess"),
            )
            rows = self.outline(database)

        self.assertEqual(written, -1)
        self.assertEqual([row["title"] for row in rows], ["Published by the source"])


if __name__ == "__main__":
    unittest.main()
