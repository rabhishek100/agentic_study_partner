"""Bounded, deterministic discovery of one public YouTube playlist."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import re
import subprocess
from typing import Protocol, Sequence
from urllib.parse import parse_qs, urlencode, urlparse


PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,100}$")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
)
MAXIMUM_LECTURES = 100
MAXIMUM_OUTPUT_BYTES = 2 * 1024 * 1024
DISCOVERY_TIMEOUT_SECONDS = 120


class PlaylistUrlError(ValueError):
    pass


class PlaylistDiscoveryError(RuntimeError):
    pass


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(
        self, argv: Sequence[str], *, timeout_seconds: int
    ) -> CommandResult: ...


@dataclass(frozen=True)
class PlaylistLecture:
    video_id: str
    title: str
    duration_seconds: int | None

    @property
    def canonical_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True)
class YouTubePlaylist:
    playlist_id: str
    canonical_url: str
    title: str
    description: str | None
    lectures: tuple[PlaylistLecture, ...]

    @property
    def known_duration_seconds(self) -> int:
        return sum(item.duration_seconds or 0 for item in self.lectures)

    @property
    def durations_complete(self) -> bool:
        return all(item.duration_seconds is not None for item in self.lectures)

    def snapshot(self) -> dict[str, object]:
        return {
            "source_kind": "youtube_playlist",
            "source_url": self.canonical_url,
            "playlist_id": self.playlist_id,
            "lecture_count": len(self.lectures),
            "known_duration_seconds": self.known_duration_seconds,
            "durations_complete": self.durations_complete,
            "allow_audio_fallback": False,
            "semantic_embeddings": False,
            "lectures": [
                {
                    "video_id": lecture.video_id,
                    "title": lecture.title,
                    "duration_seconds": lecture.duration_seconds,
                }
                for lecture in self.lectures
            ],
        }


def parse_youtube_playlist_url(value: str) -> tuple[str, str]:
    """Return the playlist ID and canonical URL, rejecting arbitrary hosts."""

    try:
        parsed = urlparse(value.strip())
    except ValueError as error:
        raise PlaylistUrlError("invalid YouTube playlist URL") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in YOUTUBE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PlaylistUrlError("playlist URL must be an HTTPS YouTube URL")
    values = parse_qs(parsed.query).get("list", [])
    if len(values) != 1 or PLAYLIST_ID.fullmatch(values[0]) is None:
        raise PlaylistUrlError("YouTube playlist URL must contain one valid list ID")
    playlist_id = values[0]
    canonical = "https://www.youtube.com/playlist?" + urlencode(
        {"list": playlist_id}
    )
    return playlist_id, canonical


def run_command(
    argv: Sequence[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=timeout_seconds,
    )


def discover_youtube_playlist(
    value: str,
    *,
    runner: CommandRunner = run_command,
    timeout_seconds: int = DISCOVERY_TIMEOUT_SECONDS,
) -> YouTubePlaylist:
    """Snapshot ordered playlist metadata without downloading video bytes."""

    playlist_id, canonical_url = parse_youtube_playlist_url(value)
    binary = os.getenv("VIDEO_YTDLP_BINARY", "yt-dlp").strip() or "yt-dlp"
    argv = (
        binary,
        "--flat-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--playlist-end",
        str(MAXIMUM_LECTURES + 1),
        "--",
        canonical_url,
    )
    try:
        completed = runner(argv, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        raise PlaylistDiscoveryError("YouTube playlist discovery timed out") from error
    except OSError as error:
        raise PlaylistDiscoveryError("yt-dlp could not be started") from error
    if completed.returncode != 0:
        raise PlaylistDiscoveryError("YouTube playlist could not be read")
    if len(completed.stdout.encode("utf-8")) > MAXIMUM_OUTPUT_BYTES:
        raise PlaylistDiscoveryError("YouTube playlist metadata is too large")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise PlaylistDiscoveryError("YouTube playlist metadata is malformed") from error
    if not isinstance(payload, dict) or payload.get("id") != playlist_id:
        raise PlaylistDiscoveryError("yt-dlp returned a different playlist")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise PlaylistDiscoveryError("YouTube playlist contains no available lectures")
    if len(raw_entries) > MAXIMUM_LECTURES:
        raise PlaylistDiscoveryError(
            f"YouTube playlists are limited to {MAXIMUM_LECTURES} lectures"
        )

    lectures: list[PlaylistLecture] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_entries, start=1):
        if not isinstance(entry, dict):
            raise PlaylistDiscoveryError("YouTube playlist metadata is malformed")
        video_id = entry.get("id")
        title = entry.get("title")
        if not isinstance(video_id, str) or VIDEO_ID.fullmatch(video_id) is None:
            raise PlaylistDiscoveryError(
                f"playlist lecture {index} is private, deleted, or malformed"
            )
        if video_id in seen:
            raise PlaylistDiscoveryError("YouTube playlist contains a duplicate lecture")
        if not isinstance(title, str) or not title.strip():
            raise PlaylistDiscoveryError(f"playlist lecture {index} has no title")
        duration = entry.get("duration")
        if duration is None:
            duration_seconds = None
        else:
            try:
                numeric_duration = float(duration)
            except (TypeError, ValueError) as error:
                raise PlaylistDiscoveryError(
                    f"playlist lecture {index} has an invalid duration"
                ) from error
            if not math.isfinite(numeric_duration) or numeric_duration <= 0:
                raise PlaylistDiscoveryError(
                    f"playlist lecture {index} has an invalid duration"
                )
            duration_seconds = max(1, round(numeric_duration))
        seen.add(video_id)
        lectures.append(
            PlaylistLecture(
                video_id=video_id,
                title=" ".join(title.split())[:500],
                duration_seconds=duration_seconds,
            )
        )

    raw_title = payload.get("title")
    title = (
        " ".join(raw_title.split())[:500]
        if isinstance(raw_title, str) and raw_title.strip()
        else f"YouTube course {playlist_id}"
    )
    raw_description = payload.get("description")
    description = (
        raw_description.strip()[:20_000]
        if isinstance(raw_description, str) and raw_description.strip()
        else None
    )
    return YouTubePlaylist(
        playlist_id=playlist_id,
        canonical_url=canonical_url,
        title=title,
        description=description,
        lectures=tuple(lectures),
    )
