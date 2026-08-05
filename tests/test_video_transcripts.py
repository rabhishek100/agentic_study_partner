"""YouTube WebVTT becomes stable timestamped canonical cues."""

import unittest

from video.transcripts import (
    InvalidTranscript,
    parse_webvtt,
    transcript_coverage,
)


class VideoTranscriptTests(unittest.TestCase):
    def test_youtube_rolling_captions_are_deduplicated_and_cleaned(self) -> None:
        cues = parse_webvtt(
            """WEBVTT

00:00.000 --> 00:02.000
hello &amp; welcome

00:02.000 --> 00:04.000 align:start position:0%
hello &amp; welcome <c>transformers</c>

00:04.000 --> 00:06.000
transformers use attention
"""
        )

        self.assertEqual(
            [cue.text for cue in cues],
            ["hello & welcome", "transformers", "use attention"],
        )
        self.assertEqual(
            [(cue.cue_index, cue.start_ms, cue.end_ms) for cue in cues],
            [(0, 0, 2_000), (1, 2_000, 4_000), (2, 4_000, 6_000)],
        )
        self.assertIn("<c>transformers</c>", cues[1].raw_text)

    def test_coverage_uses_interval_union_and_clips_to_duration(self) -> None:
        cues = parse_webvtt(
            """WEBVTT

00:00:00.000 --> 00:00:04.000
first

00:00:03.000 --> 00:00:07.000
second

00:00:09.000 --> 00:00:12.000
third
"""
        )

        self.assertEqual(transcript_coverage(cues, duration_ms=10_000), 0.8)

    def test_empty_or_backwards_captions_are_rejected(self) -> None:
        with self.assertRaises(InvalidTranscript):
            parse_webvtt("WEBVTT\n\n")
        with self.assertRaises(InvalidTranscript):
            parse_webvtt(
                "WEBVTT\n\n00:00:02.000 --> 00:00:01.000\nbackwards\n"
            )


if __name__ == "__main__":
    unittest.main()
