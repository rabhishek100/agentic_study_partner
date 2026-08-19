"""What the library list can say about a lecture without asking for detail.

The Videos page shows a poster, a chapter count, and whether slides are
attached. Those were only in the per-lecture detail payload, so a library of
thirty lectures meant thirty requests to draw one screen; they are on the
summary now, and this pins what they are supposed to contain.
"""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from video.repository import list_standalone_videos, load_standalone_video

from tests.video_fixtures import DURATION_MS, FRAME_TIMESTAMPS, publish_video_with_evidence


class VideoLibrarySummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-library.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def summary(self, database) -> dict:
        rows = list_standalone_videos(database, owner_id=self.owner)
        return next(row for row in rows if row["id"] == self.video.video_id)

    def test_the_poster_is_the_frame_nearest_the_middle_of_the_lecture(self) -> None:
        """
        The opening seconds are a title card or an empty lectern, so a library
        posted from first frames is a library of identical rectangles. The
        fixture's frames sit at 120s and 130s of a 600s lecture, so the later
        one is nearer the midpoint and wins.
        """
        with connection(self.database_url) as database:
            summary = self.summary(database)
            later = database.execute(
                """
                select id from video.frames
                where owner_id = %s and video_id = %s and timestamp_ms = %s
                """,
                (self.owner, self.video.video_id, max(FRAME_TIMESTAMPS)),
            ).fetchone()

        self.assertEqual(DURATION_MS, 600_000)
        self.assertEqual(summary["poster_frame_id"], later["id"])

    def test_a_lecture_with_no_published_version_has_no_poster(self) -> None:
        """A run still in flight must not post a frame from the version it
        replaces, and a lecture that has never published has nothing to post."""
        with connection(self.database_url) as database:
            database.execute(
                "update video.videos set current_ingestion_version_id = null "
                "where id = %s and owner_id = %s",
                (self.video.video_id, self.owner),
            )
            summary = self.summary(database)

        self.assertIsNone(summary["poster_frame_id"])

    def test_chapters_and_ready_slides_are_counted_for_the_card(self) -> None:
        with connection(self.database_url) as database:
            before = self.summary(database)
            database.execute(
                """
                insert into video.chapters (
                    owner_id, video_id, video_source_id, chapter_index,
                    chapter_kind, title, start_ms, end_ms
                ) values
                    (%(owner)s, %(video)s, %(source)s, 0, 'youtube',
                     'Setup', 0, 100000),
                    (%(owner)s, %(video)s, %(source)s, 1, 'youtube',
                     'Attention', 100000, 600000)
                """,
                {
                    "owner": self.owner,
                    "video": self.video.video_id,
                    "source": self.video.source_id,
                },
            )
            resource_id = database.execute(
                """
                insert into video.resources (
                    owner_id, resource_kind, origin, status, title, page_count
                ) values (%s, 'pdf', 'url', 'ready', 'Slides', 40)
                returning id
                """,
                (self.owner,),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.video_resources (
                    owner_id, video_id, resource_id, role, required
                ) values (%s, %s, %s, 'slides', false)
                """,
                (self.owner, self.video.video_id, resource_id),
            )
            after = self.summary(database)

        self.assertEqual(before["chapter_count"], 0)
        self.assertEqual(before["slide_count"], 0)
        self.assertEqual(after["chapter_count"], 2)
        self.assertEqual(after["slide_count"], 1)

    def test_a_slide_deck_that_is_still_processing_is_not_counted_yet(self) -> None:
        """The badge predicts whether an answer can cite the deck, and a deck
        that has not been read cannot be cited."""
        with connection(self.database_url) as database:
            resource_id = database.execute(
                """
                insert into video.resources (
                    owner_id, resource_kind, origin, status, title
                ) values (%s, 'pdf', 'upload', 'processing', 'Slides')
                returning id
                """,
                (self.owner,),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.video_resources (
                    owner_id, video_id, resource_id, role, required
                ) values (%s, %s, %s, 'slides', false)
                """,
                (self.owner, self.video.video_id, resource_id),
            )
            summary = self.summary(database)

        self.assertEqual(summary["slide_count"], 0)

    def test_the_detail_payload_carries_the_same_fields(self) -> None:
        """`_load_detail` builds on the same select, so a poster shown in the
        library cannot disappear when the same lecture is opened."""
        with connection(self.database_url) as database:
            listed = self.summary(database)
            loaded = load_standalone_video(
                database, self.video.video_id, owner_id=self.owner
            )

        self.assertEqual(loaded["poster_frame_id"], listed["poster_frame_id"])
        self.assertEqual(loaded["chapter_count"], listed["chapter_count"])


if __name__ == "__main__":
    unittest.main()
