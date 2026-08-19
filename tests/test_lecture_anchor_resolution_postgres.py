"""Lecture anchor resolution against a real database.

The counterpart of `test_anchor_resolution_postgres.py`: `test_video_anchors.py`
covers the window arithmetic with a faked connection, and this covers what
cannot be faked — that the SQL is valid, that a moment selects the units a real
ingestion produced, and that resolution is pinned to the published version.
"""

import unittest
from uuid import uuid4

from storage.database import connection, resolve_database_url
from study.contracts import LectureMomentAnchor, LectureStretchAnchor
from tests.video_fixtures import FRAME_TIMESTAMPS, publish_video_with_evidence
from video.anchors import resolve_lecture_anchors

# The fixture's transcript cue runs 0–10s and its frames sit at 2:00 and 2:10.
TRANSCRIPT_MS = 5_000
FRAME_MS = FRAME_TIMESTAMPS[0]


class LectureAnchorResolutionPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@lecture-anchor.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def resolve(self, anchor):
        with connection(self.database_url) as database:
            (resolved,) = resolve_lecture_anchors(
                database,
                [anchor],
                owner_id=self.owner,
                video_id=self.video.video_id,
            )
        return resolved

    def test_a_moment_resolves_to_the_units_around_it(self):
        resolved = self.resolve(
            LectureMomentAnchor(
                anchor_id="a1",
                video_id=self.video.video_id,
                timestamp_ms=FRAME_MS,
            )
        )

        self.assertTrue(resolved.matched)
        self.assertTrue(resolved.evidence_ids)
        self.assertEqual(resolved.label, "2:00")

    def starts(self, evidence_ids):
        """When each resolved unit begins, so a window can be checked."""

        if not evidence_ids:
            return set()
        with connection(self.database_url) as database:
            rows = database.execute(
                "select start_ms from video.evidence_units where id = any(%s)",
                (list(evidence_ids),),
            ).fetchall()
        return {row["start_ms"] for row in rows}

    def test_the_window_reaches_backwards_further_than_forwards(self):
        # A question asked at 12:04 is nearly always about what was just said,
        # so the same eighty seconds of distance resolves differently depending
        # on which side of the playhead the material is.
        behind = self.resolve(
            LectureMomentAnchor(
                anchor_id="a1",
                video_id=self.video.video_id,
                timestamp_ms=FRAME_MS + 80_000,
            )
        )
        ahead = self.resolve(
            LectureMomentAnchor(
                anchor_id="a1",
                video_id=self.video.video_id,
                timestamp_ms=FRAME_MS - 80_000,
            )
        )

        self.assertIn(FRAME_MS, self.starts(behind.evidence_ids))
        self.assertNotIn(FRAME_MS, self.starts(ahead.evidence_ids))

    def test_a_stretch_takes_exactly_what_it_spans(self):
        resolved = self.resolve(
            LectureStretchAnchor(
                anchor_id="a1",
                video_id=self.video.video_id,
                start_ms=FRAME_TIMESTAMPS[0] - 1_000,
                end_ms=FRAME_TIMESTAMPS[1] + 1_000,
            )
        )

        self.assertTrue(resolved.matched)
        self.assertTrue(resolved.evidence_ids)
        self.assertEqual(resolved.label, "1:59 – 2:11")

    def test_a_stretch_past_the_end_of_the_lecture_matches_nothing(self):
        resolved = self.resolve(
            LectureStretchAnchor(
                anchor_id="a1",
                video_id=self.video.video_id,
                start_ms=590_000,
                end_ms=600_000,
            )
        )

        self.assertFalse(resolved.matched)
        self.assertEqual(resolved.evidence_ids, ())

    def test_another_owners_lecture_resolves_to_nothing(self):
        with connection(self.database_url) as database:
            (resolved,) = resolve_lecture_anchors(
                database,
                [
                    LectureMomentAnchor(
                        anchor_id="a1",
                        video_id=self.video.video_id,
                        timestamp_ms=FRAME_MS,
                    )
                ],
                owner_id=uuid4(),
                video_id=self.video.video_id,
            )

        self.assertFalse(resolved.matched)
        self.assertEqual(resolved.evidence_ids, ())


if __name__ == "__main__":
    unittest.main()
