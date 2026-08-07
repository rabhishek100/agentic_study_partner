"""What "the transcript covers the lecture" is allowed to mean.

The gate this replaces summed cue occupancy and required 95%. The production
lecture scored 93.84% with every other gate passing, and was published
degraded. Measuring the missing 6.16%: 376.7 seconds across 2,325 separate
gaps, 2,212 of them under a second, the largest anywhere 26 seconds, cues
running from 9.1 seconds in to 2.6 seconds before the end. Nothing was
missing — the lecturer breathes, and a cue-timed transcript has no cues over
silence.

These tests are written against that shape, because a threshold calibrated on
one lecture should at least be pinned to the measurements that calibrated it.
"""

import unittest

from video.evidence_store import (
    TRANSCRIPT_GAP_LIMIT_MS,
    TRANSCRIPT_HARD_GAP_LIMIT_MS,
    _maximum_transcript_gap,
)
from video.readiness import readiness_notes


class FakeConnection:
    """Just enough connection to hand back transcript segments."""

    def __init__(self, segments):
        self.segments = [
            {"start_ms": start, "end_ms": end} for start, end in segments
        ]

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return self.segments


def gap(segments, *, duration_ms, transcript_id="t"):
    return _maximum_transcript_gap(
        FakeConnection(segments),
        owner_id="o",
        video_id="v",
        transcript_id=transcript_id,
        duration_ms=duration_ms,
    )


class TranscriptGapTests(unittest.TestCase):
    def test_many_small_pauses_are_not_a_gap(self) -> None:
        """The production case, in miniature.

        Ten minutes of speech broken by 200 half-second breaths loses 100
        seconds of occupancy and leaves nothing untranscribed.
        """

        segments = []
        cursor = 0
        for _ in range(200):
            segments.append((cursor, cursor + 2_500))
            cursor += 3_000
        duration = cursor

        self.assertEqual(gap(segments, duration_ms=duration), 500)
        self.assertLessEqual(gap(segments, duration_ms=duration), TRANSCRIPT_GAP_LIMIT_MS)
        # Occupancy over the same transcript is 83%, which the old gate failed
        # and which says nothing at all about what is missing.
        occupancy = sum(end - start for start, end in segments) / duration
        self.assertLess(occupancy, 0.95)

    def test_one_real_hole_is_a_gap(self) -> None:
        segments = [(0, 60_000), (400_000, 460_000)]
        self.assertEqual(gap(segments, duration_ms=460_000), 340_000)
        self.assertGreater(gap(segments, duration_ms=460_000), TRANSCRIPT_GAP_LIMIT_MS)

    def test_a_transcript_that_starts_late_is_missing_the_opening(self) -> None:
        # No interior gap at all, and ten minutes absent. A gate on interior
        # gaps alone would wave this through.
        self.assertEqual(
            gap([(600_000, 1_200_000)], duration_ms=1_200_000), 600_000
        )

    def test_a_transcript_that_stops_early_is_missing_the_close(self) -> None:
        self.assertEqual(gap([(0, 600_000)], duration_ms=1_200_000), 600_000)

    def test_overlapping_cues_do_not_invent_a_gap(self) -> None:
        # A long cue followed by one that starts inside it covers the span
        # continuously; comparing each cue only with the one before it would
        # report a negative gap or, worse, reset the coverage mark backwards.
        self.assertEqual(
            gap([(0, 90_000), (10_000, 20_000), (90_000, 120_000)],
                duration_ms=120_000),
            0,
        )

    def test_no_transcript_is_not_a_passing_zero(self) -> None:
        self.assertIsNone(gap([], duration_ms=1_000))
        self.assertIsNone(gap([(0, 10)], duration_ms=10, transcript_id=None))

    def test_the_hard_floor_sits_well_above_the_quality_gate(self) -> None:
        # Refusing to publish is a heavier answer than publishing with a
        # reservation, so the two thresholds must not be the same number.
        self.assertGreater(TRANSCRIPT_HARD_GAP_LIMIT_MS, TRANSCRIPT_GAP_LIMIT_MS)


class TranscriptNoteTests(unittest.TestCase):
    GATES = {
        "canonical_source": True,
        "transcript_complete": False,
        "timeline_frames": True,
        "visual_analysis_success": True,
        "every_chapter_visual": True,
        "no_visual_gap_over_five_minutes": True,
        "transcript_evidence_complete": True,
        "visual_evidence_present": True,
        "semantic_index_complete": True,
        "required_resources_ready": True,
    }

    def test_a_cue_timed_failure_names_the_stretch_that_is_missing(self) -> None:
        notes = readiness_notes(
            {
                "gates": self.GATES,
                "duration_ms": 6_118_760,
                "maximum_transcript_gap_ms": 340_000,
                "transcript_completeness_ratio": 0.938432,
            }
        )
        self.assertEqual(len(notes), 1)
        self.assertIn("6 minutes", notes[0])
        # The share is no longer what failed, so it is no longer what is
        # reported: "covers 94%" reads as a defect where none exists.
        self.assertNotIn("94%", notes[0])

    def test_hosted_asr_still_reports_the_share_it_processed(self) -> None:
        notes = readiness_notes(
            {
                "gates": self.GATES,
                "duration_ms": 6_118_760,
                "transcript_completeness_ratio": 0.80,
            }
        )
        self.assertIn("80%", notes[0])


if __name__ == "__main__":
    unittest.main()
