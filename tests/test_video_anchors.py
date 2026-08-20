"""Resolving a moment or a stretch of lecture to canonical evidence units.

The book's rules one level up, with two differences that carry the weight: a
unit belongs to an ingestion version, and a moment is a window rather than an
instant — biased backwards, because a question asked at 12:04 is about what
was just said.
"""

import unittest
from types import SimpleNamespace
from uuid import UUID, uuid4

from study.contracts import LectureMomentAnchor, LectureStretchAnchor
from video.anchors import (
    MAX_UNITS_PER_ANCHOR,
    MOMENT_LOOKAHEAD_MS,
    MOMENT_LOOKBACK_MS,
    resolve_lecture_anchors,
    timestamp,
)

OWNER = uuid4()
VIDEO = UUID("8b1f3c4e-0000-4000-8000-000000000001")
OTHER_VIDEO = UUID("8b1f3c4e-0000-4000-8000-000000000002")
VERSION = UUID("8b1f3c4e-0000-4000-8000-0000000000ff")


def unit(identifier: str, start_ms: int, modality: str = "transcript") -> dict:
    return {
        "id": identifier,
        "modality": modality,
        "start_ms": start_ms,
        "end_ms": start_ms + 8_000,
    }


class FakeConnection:
    """Answers the version lookup and the window query, and records both."""

    def __init__(self, units: list[dict], published: bool = True):
        self.units = units
        self.published = published
        self.parameters: list[tuple] = []

    def execute(self, sql: str, parameters: tuple):
        self.parameters.append(parameters)
        if "ingestion_versions" in sql:
            rows = [{"id": VERSION}] if self.published else []
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None)
        return SimpleNamespace(fetchall=lambda: self.units)


class TimestampTests(unittest.TestCase):
    def test_reads_the_way_a_player_shows_it(self):
        self.assertEqual(timestamp(724_000), "12:04")
        self.assertEqual(timestamp(0), "0:00")

    def test_grows_an_hour_field_only_when_there_is_one(self):
        self.assertEqual(timestamp(4_360_000), "1:12:40")


class MomentTests(unittest.TestCase):
    def moment(self, timestamp_ms: int = 724_000) -> LectureMomentAnchor:
        return LectureMomentAnchor(
            anchor_id="a1",
            video_id=VIDEO,
            timestamp_ms=timestamp_ms,
        )

    def test_the_window_looks_further_back_than_forward(self):
        # A question asked at 12:04 is about what was just said. A symmetric
        # window would spend half its budget on unheard content.
        connection = FakeConnection([unit("u1", 700_000)])
        resolve_lecture_anchors(
            connection,
            [self.moment()],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        window = connection.parameters[-1]
        self.assertEqual(window[-2], 724_000 + MOMENT_LOOKAHEAD_MS)
        self.assertEqual(window[-1], 724_000 - MOMENT_LOOKBACK_MS)

    def test_the_window_never_starts_before_the_lecture(self):
        connection = FakeConnection([unit("u1", 0)])
        resolve_lecture_anchors(
            connection,
            [self.moment(timestamp_ms=10_000)],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(connection.parameters[-1][-1], 0)

    def test_units_in_the_window_become_the_anchor(self):
        connection = FakeConnection([unit("u1", 700_000), unit("u2", 712_000)])
        (resolved,) = resolve_lecture_anchors(
            connection,
            [self.moment()],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(resolved.evidence_ids, ("u1", "u2"))
        self.assertTrue(resolved.matched)
        self.assertEqual(resolved.label, "12:04")

    def test_a_silent_window_reports_no_match(self):
        connection = FakeConnection([])
        (resolved,) = resolve_lecture_anchors(
            connection,
            [self.moment()],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(resolved.evidence_ids, ())
        self.assertFalse(resolved.matched)


class StretchTests(unittest.TestCase):
    def stretch(self) -> LectureStretchAnchor:
        return LectureStretchAnchor(
            anchor_id="a1",
            video_id=VIDEO,
            start_ms=700_000,
            end_ms=750_000,
        )

    def test_a_stretch_is_taken_exactly_as_marked(self):
        connection = FakeConnection([unit("u1", 700_000)])
        (resolved,) = resolve_lecture_anchors(
            connection,
            [self.stretch()],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(connection.parameters[-1][-2:], (750_000, 700_000))
        self.assertEqual(resolved.label, "11:40 – 12:30")

    def test_a_long_stretch_keeps_a_frame_rather_than_only_speech(self):
        # A dense run of transcript would otherwise fill the cap on its own and
        # push out the frame a question about a diagram needs.
        units = [unit(f"t{index}", 700_000 + index * 1_000) for index in range(12)]
        units.append(unit("frame", 706_000, modality="visual_frame"))
        connection = FakeConnection(units)
        (resolved,) = resolve_lecture_anchors(
            connection,
            [self.stretch()],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(len(resolved.evidence_ids), MAX_UNITS_PER_ANCHOR)
        self.assertIn("frame", resolved.evidence_ids)
        self.assertTrue(resolved.dropped)


class VersionAndIdentityTests(unittest.TestCase):
    def test_resolution_is_scoped_to_the_published_version(self):
        connection = FakeConnection([unit("u1", 700_000)])
        resolve_lecture_anchors(
            connection,
            [LectureMomentAnchor(anchor_id="a1", video_id=VIDEO, timestamp_ms=724_000)],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(connection.parameters[-1][2], VERSION)

    def test_an_unpublished_lecture_pins_nothing(self):
        connection = FakeConnection([unit("u1", 700_000)], published=False)
        (resolved,) = resolve_lecture_anchors(
            connection,
            [LectureMomentAnchor(anchor_id="a1", video_id=VIDEO, timestamp_ms=724_000)],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(resolved.evidence_ids, ())
        self.assertFalse(resolved.matched)

    def test_an_anchor_naming_another_lecture_is_refused_not_reinterpreted(self):
        # Resolving it here would produce plausible evidence for the wrong
        # recording, which is worse than having no anchor.
        connection = FakeConnection([unit("u1", 700_000)])
        (resolved,) = resolve_lecture_anchors(
            connection,
            [
                LectureMomentAnchor(
                    anchor_id="a1",
                    video_id=OTHER_VIDEO,
                    timestamp_ms=724_000,
                )
            ],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        self.assertEqual(resolved.evidence_ids, ())
        self.assertFalse(resolved.matched)
        self.assertTrue(resolved.dropped)


class AsSourceTests(unittest.TestCase):
    def test_a_lecture_anchor_offers_no_transcription_of_its_own(self):
        # The reader marked time, not words. The words are the transcript units
        # the answer will cite, so there is nothing to quote separately and
        # nothing that could fail to match.
        connection = FakeConnection([unit("u1", 700_000)])
        (resolved,) = resolve_lecture_anchors(
            connection,
            [LectureMomentAnchor(anchor_id="a1", video_id=VIDEO, timestamp_ms=724_000)],
            owner_id=OWNER,
            video_id=VIDEO,
        )
        source = resolved.as_source()
        self.assertEqual(source.identities, ("u1",))
        self.assertEqual(source.selected_text, "")
        self.assertEqual(source.label, "12:04")


if __name__ == "__main__":
    unittest.main()
