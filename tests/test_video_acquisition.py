"""YouTube acquisition treats external commands and their files as untrusted."""

from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.video_fixtures import encoded_video_bytes
from video.acquisition import (
    AcquisitionError,
    acquire_youtube,
    chapters_from_metadata,
    probe_media,
    run_command,
)


VIDEO_ID = "dQw4w9WgXcQ"


@dataclass
class Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class RecordingRunner:
    def __init__(self, root: Path, *, returned_id: str = VIDEO_ID) -> None:
        self.root = root
        self.returned_id = returned_id
        self.calls: list[tuple[tuple[str, ...], int]] = []

    def __call__(self, argv: tuple[str, ...], *, timeout_seconds: int) -> Result:
        self.calls.append((argv, timeout_seconds))
        if argv[0] == "yt-dlp":
            (self.root / "source.mp4").write_bytes(encoded_video_bytes())
            (self.root / "source.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
            (self.root / "source.info.json").write_text(
                json.dumps(
                    {
                        "id": self.returned_id,
                        "title": "Lecture 1",
                        "description": "Course lecture",
                        "chapters": [
                            {"title": "Intro", "start_time": 0, "end_time": 5},
                            {"title": "Model", "start_time": 5, "end_time": 10},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return Result()
        return Result(
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 1920,
                            "height": 1080,
                        },
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                    "format": {"duration": "10.0", "format_name": "mov,mp4"},
                }
            )
        )


class VideoAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_download_is_one_bounded_video_with_english_vtt(self) -> None:
        runner = RecordingRunner(self.root)
        source = acquire_youtube(
            f"https://youtu.be/{VIDEO_ID}",
            self.root,
            expected_video_id=VIDEO_ID,
            runner=runner,
            maximum_bytes=123_456,
        )

        download, _ = runner.calls[0]
        self.assertEqual(download[0], "yt-dlp")
        self.assertIn("--no-playlist", download)
        self.assertNotIn("--max-downloads", download)
        self.assertIn("--continue", download)
        self.assertIn("--part", download)
        self.assertEqual(
            download[download.index("--http-chunk-size") + 1], "5242880"
        )
        # H.264 is requested first: the deployed image cannot decode the AV1
        # that "best video under 1080p" now resolves to on YouTube.
        requested_format = download[download.index("--format") + 1]
        self.assertTrue(requested_format.startswith("bv*[vcodec^=avc1]"))
        self.assertIn("/bv*[height<=1080]+ba/b[height<=1080]", requested_format)
        self.assertEqual(download[download.index("--max-filesize") + 1], "123456")
        self.assertIn("--write-subs", download)
        self.assertIn("--write-auto-subs", download)
        self.assertEqual(download[download.index("--sub-langs") + 1], "en.*")
        self.assertEqual(download[download.index("--sub-format") + 1], "vtt")
        self.assertEqual(download[-2], "--")
        self.assertEqual(download[-1], f"https://www.youtube.com/watch?v={VIDEO_ID}")
        self.assertNotIn("shell", download)

        probe, _ = runner.calls[1]
        self.assertIsInstance(probe, tuple)
        self.assertEqual(probe[0], "ffprobe")
        self.assertEqual(source.video_id, VIDEO_ID)
        self.assertEqual(source.media.duration_ms, 10_000)
        self.assertEqual(
            source.caption_paths, ((self.root / "source.en.vtt").resolve(),)
        )
        self.assertEqual(
            [(chapter.title, chapter.start_ms, chapter.end_ms) for chapter in source.chapters],
            [("Intro", 0, 5_000), ("Model", 5_000, 10_000)],
        )

    def test_url_identity_is_checked_before_downloading(self) -> None:
        runner = RecordingRunner(self.root)
        with self.assertRaisesRegex(AcquisitionError, "source URL"):
            acquire_youtube(
                "https://youtu.be/9bZkp7q19f0",
                self.root,
                expected_video_id=VIDEO_ID,
                runner=runner,
            )
        self.assertEqual(runner.calls, [])

    def test_a_failed_download_reports_why_without_leaking_secrets(self) -> None:
        stderr = (
            "WARNING: [youtube] n challenge solving failed\n"
            "ERROR: [youtube] Ub3GoFaUcds: Sign in to confirm you are not a bot; "
            "cookies at /home/worker/secrets/cookies.txt, url "
            "https://rr3.googlevideo.com/playback?sig=SECRET, token "
            "abcdefghijklmnopqrstuvwxyz0123456789\n"
        )

        def failing(argv: tuple[str, ...], *, timeout_seconds: int) -> Result:
            del argv, timeout_seconds
            return Result(returncode=1, stderr=stderr)

        with self.assertRaises(AcquisitionError) as caught:
            acquire_youtube(
                f"https://www.youtube.com/watch?v={VIDEO_ID}",
                self.root,
                expected_video_id=VIDEO_ID,
                runner=failing,
            )

        message = str(caught.exception)
        # The operator needs the provider's own sentence: without it, a blocked
        # request and a missing format are the same unreadable failure.
        self.assertIn("Sign in to confirm you are not a bot", message)
        self.assertNotIn("googlevideo.com", message)
        self.assertNotIn("SECRET", message)
        self.assertNotIn("/home/worker/secrets", message)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", message)
        self.assertLessEqual(len(message), 700)

    def test_a_silent_tool_failure_still_names_the_tool(self) -> None:
        def failing(argv: tuple[str, ...], *, timeout_seconds: int) -> Result:
            del argv, timeout_seconds
            return Result(returncode=1, stderr="   \n  ")

        with self.assertRaisesRegex(AcquisitionError, r"^yt-dlp failed$"):
            acquire_youtube(
                f"https://www.youtube.com/watch?v={VIDEO_ID}",
                self.root,
                expected_video_id=VIDEO_ID,
                runner=failing,
            )

    def test_downloaded_identity_must_match_expected_video(self) -> None:
        runner = RecordingRunner(self.root, returned_id="9bZkp7q19f0")
        with self.assertRaisesRegex(AcquisitionError, "different YouTube video"):
            acquire_youtube(
                f"https://www.youtube.com/watch?v={VIDEO_ID}",
                self.root,
                expected_video_id=VIDEO_ID,
                runner=runner,
            )
        self.assertEqual(len(runner.calls), 1)

    def test_malformed_downloader_metadata_is_rejected(self) -> None:
        def malformed(argv: tuple[str, ...], *, timeout_seconds: int) -> Result:
            del argv, timeout_seconds
            (self.root / "source.info.json").write_text("[]", encoding="utf-8")
            return Result()

        with self.assertRaisesRegex(AcquisitionError, "metadata is malformed"):
            acquire_youtube(
                f"https://youtu.be/{VIDEO_ID}",
                self.root,
                expected_video_id=VIDEO_ID,
                runner=malformed,
            )

    def test_probe_requires_json_video_stream_and_positive_duration(self) -> None:
        path = self.root / "source.mp4"
        path.write_bytes(encoded_video_bytes())
        invalid_outputs = (
            "not-json",
            json.dumps({"streams": [], "format": {"duration": "10"}}),
            json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 1280,
                            "height": 720,
                        }
                    ],
                    "format": {"duration": "0"},
                }
            ),
        )
        for stdout in invalid_outputs:
            with self.subTest(stdout=stdout), self.assertRaises(AcquisitionError):
                probe_media(
                    path,
                    runner=lambda argv, *, timeout_seconds: Result(stdout=stdout),
                )

    def test_probe_uses_an_argv_vector_without_a_shell(self) -> None:
        path = self.root / "a video; touch nope.mp4"
        path.write_bytes(encoded_video_bytes())
        calls: list[tuple[str, ...]] = []

        def runner(argv: tuple[str, ...], *, timeout_seconds: int) -> Result:
            del timeout_seconds
            calls.append(argv)
            return Result(
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "vp9",
                                "width": 640,
                                "height": 360,
                            }
                        ],
                        "format": {"duration": 1.25},
                    }
                )
            )

        metadata = probe_media(path, runner=runner)
        self.assertEqual(calls[0][-1], str(path))
        self.assertIsInstance(calls[0], tuple)
        self.assertEqual(metadata.duration_ms, 1_250)

    @patch("video.acquisition.subprocess.run")
    def test_default_runner_explicitly_disables_the_shell(self, mocked_run) -> None:
        mocked_run.return_value = Result()
        run_command(("ffprobe", "unsafe; value"), timeout_seconds=12)
        mocked_run.assert_called_once_with(
            ["ffprobe", "unsafe; value"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=12,
        )

    def test_inconsistent_chapter_intervals_are_rejected(self) -> None:
        malformed = {
            "chapters": [
                {"title": "First", "start_time": 0, "end_time": 7},
                {"title": "Second", "start_time": 5, "end_time": 10},
            ]
        }
        with self.assertRaisesRegex(AcquisitionError, "intervals are inconsistent"):
            chapters_from_metadata(malformed, duration_ms=10_000)

    def test_missing_chapter_end_is_derived_from_next_boundary(self) -> None:
        metadata = {
            "chapters": [
                {"title": "First", "start_time": 0},
                {"title": "Second", "start_time": 2.5},
            ]
        }
        chapters = chapters_from_metadata(metadata, duration_ms=5_000)
        self.assertEqual(
            [(chapter.start_ms, chapter.end_ms) for chapter in chapters],
            [(0, 2_500), (2_500, 5_000)],
        )


if __name__ == "__main__":
    unittest.main()
