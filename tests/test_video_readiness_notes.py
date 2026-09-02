"""Turning a failed quality gate into something a reader can act on."""

import unittest

from video.readiness import readiness_notes


def gates(**overrides) -> dict:
    passing = {
        "canonical_source": True,
        "transcript_complete": True,
        "timeline_frames": True,
        "visual_analysis_success": True,
        "every_chapter_visual": True,
        "no_visual_gap_over_five_minutes": True,
        "transcript_evidence_complete": True,
        "visual_evidence_present": True,
        "semantic_index_complete": True,
        "required_resources_ready": True,
    }
    passing.update(overrides)
    return passing


class ReadinessNotesTests(unittest.TestCase):
    def test_a_version_that_passed_everything_says_nothing(self) -> None:
        self.assertEqual(readiness_notes({"gates": gates()}), [])

    def test_a_version_with_no_measurements_says_nothing(self) -> None:
        # A reservation nobody can explain is worse than no reservation, so a
        # version recorded before gates existed is simply ready.
        self.assertEqual(readiness_notes(None), [])
        self.assertEqual(readiness_notes({}), [])
        self.assertEqual(readiness_notes({"gates": "not a mapping"}), [])

    def test_an_incomplete_transcript_reports_the_share_and_the_time(self) -> None:
        """The production case: 93.8% against a 95% threshold."""

        notes = readiness_notes(
            {
                "gates": gates(transcript_complete=False),
                "transcript_completeness_ratio": 0.938432,
                "duration_ms": 6_118_760,
            }
        )

        self.assertEqual(len(notes), 1)
        self.assertIn("94%", notes[0])
        # Six minutes of a hundred-minute lecture is the fact that matters;
        # "Ready (partial)" carried neither number.
        self.assertIn("6 minutes", notes[0])

    def test_a_near_complete_transcript_does_not_claim_missing_minutes(self) -> None:
        notes = readiness_notes(
            {
                "gates": gates(transcript_complete=False),
                "transcript_completeness_ratio": 0.999,
                "duration_ms": 600_000,
            }
        )

        self.assertNotIn("minute", notes[0])

    def test_failed_visual_analysis_counts_the_frames(self) -> None:
        notes = readiness_notes(
            {
                "gates": gates(visual_analysis_success=False),
                "frame_count": 258,
                "successful_visual_observation_count": 190,
            }
        )

        self.assertEqual(notes, ["190 of 258 captured frames could be interpreted."])

    def test_course_visual_cap_reports_only_frames_selected_for_analysis(self) -> None:
        notes = readiness_notes(
            {
                "gates": gates(visual_analysis_success=False),
                "frame_count": 258,
                "visual_analysis_target_count": 200,
                "successful_visual_observation_count": 190,
            }
        )

        self.assertEqual(notes, ["190 of 200 selected frames could be interpreted."])

    def test_a_visual_gap_is_reported_in_minutes(self) -> None:
        notes = readiness_notes(
            {
                "gates": gates(no_visual_gap_over_five_minutes=False),
                "maximum_visual_gap_ms": 420_000,
            }
        )

        self.assertIn("7 minutes", notes[0])

    def test_an_incomplete_semantic_index_says_what_still_works(self) -> None:
        notes = readiness_notes(
            {
                "gates": gates(semantic_index_complete=False),
                "evidence_count": 2911,
                "text_embedding_count": 1200,
            }
        )

        self.assertIn("1200 of 2911", notes[0])
        # Keyword search still answers, and saying so is the difference
        # between a caveat and an alarm.
        self.assertIn("keyword", notes[0])

    def test_several_failures_lead_with_the_most_limiting(self) -> None:
        notes = readiness_notes(
            {
                "gates": gates(
                    semantic_index_complete=False,
                    visual_evidence_present=False,
                    transcript_complete=False,
                ),
                "transcript_completeness_ratio": 0.5,
                "duration_ms": 600_000,
                "evidence_count": 10,
                "text_embedding_count": 1,
            }
        )

        self.assertEqual(len(notes), 3)
        self.assertIn("No frames were captured", notes[0])

    def test_a_missing_measurement_still_produces_a_sentence(self) -> None:
        # The gate failed; the reader is told so even when the metric behind
        # it is absent, rather than being told nothing.
        notes = readiness_notes({"gates": gates(visual_analysis_success=False)})

        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].endswith("."))


if __name__ == "__main__":
    unittest.main()
