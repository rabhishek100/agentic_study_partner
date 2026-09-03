"""Playlist discovery is bounded before it creates any ingestion jobs."""

from dataclasses import dataclass
import json
import subprocess
import unittest

from video.playlists import (
    PlaylistDiscoveryError,
    PlaylistUrlError,
    discover_youtube_playlist,
    parse_youtube_playlist_url,
)


PLAYLIST_ID = "PLrw6a1wE39_tb2fErI4-WkMbsvGQk9_UB"


@dataclass
class Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class PlaylistDiscoveryTests(unittest.TestCase):
    def test_discovers_an_ordered_snapshot_without_downloading_media(self) -> None:
        calls = []
        payload = {
            "id": PLAYLIST_ID,
            "title": "  Distributed   Systems  ",
            "description": "Course description",
            "entries": [
                {"id": "abcdefghijk", "title": "Lecture 1", "duration": 3600.2},
                {"id": "lmnopqrstuv", "title": "Lecture 2", "duration": 1800},
            ],
        }

        def runner(argv, *, timeout_seconds):
            calls.append((tuple(argv), timeout_seconds))
            return Result(stdout=json.dumps(payload))

        playlist = discover_youtube_playlist(
            f"https://www.youtube.com/playlist?list={PLAYLIST_ID}&feature=share",
            runner=runner,
        )

        self.assertEqual(playlist.title, "Distributed Systems")
        self.assertEqual(playlist.known_duration_seconds, 5400)
        self.assertTrue(playlist.durations_complete)
        self.assertEqual(
            [lecture.video_id for lecture in playlist.lectures],
            ["abcdefghijk", "lmnopqrstuv"],
        )
        command, timeout = calls[0]
        self.assertIn("--flat-playlist", command)
        self.assertIn("--dump-single-json", command)
        self.assertEqual(command[command.index("--playlist-end") + 1], "101")
        self.assertEqual(command[-2], "--")
        self.assertEqual(timeout, 120)

    def test_rejects_non_youtube_hosts_before_running_a_command(self) -> None:
        with self.assertRaises(PlaylistUrlError):
            parse_youtube_playlist_url(
                f"https://example.test/playlist?list={PLAYLIST_ID}"
            )

    def test_rejects_duplicates_and_private_entries(self) -> None:
        cases = (
            [
                {"id": "abcdefghijk", "title": "One", "duration": 10},
                {"id": "abcdefghijk", "title": "Again", "duration": 10},
            ],
            [{"id": None, "title": "Private video", "duration": None}],
        )
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(
                PlaylistDiscoveryError
            ):
                discover_youtube_playlist(
                    f"https://www.youtube.com/playlist?list={PLAYLIST_ID}",
                    runner=lambda *args, **kwargs: Result(
                        stdout=json.dumps(
                            {"id": PLAYLIST_ID, "title": "Course", "entries": entries}
                        )
                    ),
                )

    def test_timeout_is_a_safe_discovery_failure(self) -> None:
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("yt-dlp", 120)

        with self.assertRaisesRegex(PlaylistDiscoveryError, "timed out"):
            discover_youtube_playlist(
                f"https://www.youtube.com/playlist?list={PLAYLIST_ID}",
                runner=timeout,
            )


if __name__ == "__main__":
    unittest.main()
