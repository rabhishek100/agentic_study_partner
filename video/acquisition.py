"""Bounded, shell-free acquisition of one canonical YouTube video.

The downloader and probe are deliberately behind an injectable runner.  This
module owns validation of their untrusted JSON and filesystem outputs; callers
only receive immutable values that are safe to persist.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Protocol

from video.sources import YOUTUBE_ID, parse_youtube_url


MAXIMUM_HEIGHT = 1080
DEFAULT_MAXIMUM_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_METADATA_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 2 * 60 * 60
PROBE_TIMEOUT_SECONDS = 30
VIDEO_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"
ENGLISH_SUBTITLE_LANGUAGES = "en.*"


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., CommandResult]


class AcquisitionError(RuntimeError):
    """A downloader, probe, or returned artifact failed validation."""


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    start_ms: int
    end_ms: int


# An explicit name remains useful at call sites that also handle book chapters.
VideoChapter = Chapter


@dataclass(frozen=True)
class MediaMetadata:
    duration_ms: int
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    format_name: str | None
    size_bytes: int


@dataclass(frozen=True)
class DownloadedSource:
    video_id: str
    title: str
    description: str
    video_path: Path
    info_path: Path
    caption_paths: tuple[Path, ...]
    media: MediaMetadata
    chapters: tuple[Chapter, ...]

    @property
    def media_metadata(self) -> MediaMetadata:
        return self.media


def run_command(
    argv: Sequence[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    """Run an argv vector without invoking a shell."""

    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=timeout_seconds,
    )


def acquire_youtube(
    source_url: str,
    destination: Path,
    *,
    expected_video_id: str,
    runner: CommandRunner = run_command,
    maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
) -> DownloadedSource:
    """Download and validate exactly one expected YouTube video.

    ``destination`` is a job-specific staging directory.  A fixed output stem
    keeps remote titles out of local paths and makes returned files enumerable.
    """

    if not YOUTUBE_ID.fullmatch(expected_video_id):
        raise ValueError("expected_video_id must be one YouTube video ID")
    parsed_source = parse_youtube_url(source_url)
    if parsed_source.video_id != expected_video_id:
        raise AcquisitionError("source URL does not match the expected YouTube ID")
    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive")

    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise AcquisitionError("acquisition destination must be a real directory")
    root = root.resolve()
    output_template = root / "source.%(ext)s"

    argv = [
        os.getenv("VIDEO_YTDLP_BINARY", "yt-dlp").strip() or "yt-dlp",
        "--no-playlist",
        "--max-downloads",
        "1",
        "--format",
        VIDEO_FORMAT,
        "--max-filesize",
        str(maximum_bytes),
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        ENGLISH_SUBTITLE_LANGUAGES,
        "--sub-format",
        "vtt",
        "--output",
        str(output_template),
        "--",
        parsed_source.canonical_url,
    ]
    completed = _execute(
        runner,
        argv,
        timeout_seconds=int(
            os.getenv("VIDEO_ACQUISITION_TIMEOUT_SECONDS", DOWNLOAD_TIMEOUT_SECONDS)
        ),
    )
    if completed.returncode != 0:
        raise AcquisitionError(_command_failure("yt-dlp", completed.stderr))

    info_path = root / "source.info.json"
    info = _read_json_object(info_path, label="yt-dlp metadata")
    returned_id = info.get("id")
    if returned_id != expected_video_id:
        raise AcquisitionError("yt-dlp returned a different YouTube video")
    title = info.get("title")
    if not isinstance(title, str) or not title.strip():
        raise AcquisitionError("yt-dlp metadata has no title")
    description = info.get("description")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise AcquisitionError("yt-dlp metadata has an invalid description")

    video_path = root / "source.mp4"
    _require_regular_file(video_path, root=root, label="downloaded video")
    size_bytes = video_path.stat().st_size
    if size_bytes <= 0:
        raise AcquisitionError("downloaded video is empty")
    if size_bytes > maximum_bytes:
        raise AcquisitionError("downloaded video exceeds the configured size limit")

    media = probe_media(video_path, runner=runner)
    if media.height > MAXIMUM_HEIGHT:
        raise AcquisitionError("downloaded video exceeds the 1080p limit")
    chapters = chapters_from_metadata(info, duration_ms=media.duration_ms)
    captions = tuple(
        path
        for path in sorted(root.glob("source*.vtt"))
        if _is_regular_file_within(path, root)
    )
    return DownloadedSource(
        video_id=expected_video_id,
        title=title.strip(),
        description=description,
        video_path=video_path,
        info_path=info_path,
        caption_paths=captions,
        media=media,
        chapters=chapters,
    )


def probe_media(
    path: Path, *, runner: CommandRunner = run_command
) -> MediaMetadata:
    """Return validated ffprobe metadata for a local regular video file."""

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise AcquisitionError("media probe source must be a regular file")
    argv = [
        os.getenv("VIDEO_FFPROBE_BINARY", "ffprobe").strip() or "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(source),
    ]
    completed = _execute(runner, argv, timeout_seconds=PROBE_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise AcquisitionError(_command_failure("ffprobe", completed.stderr))
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise AcquisitionError("ffprobe returned malformed JSON") from error
    if not isinstance(payload, dict):
        raise AcquisitionError("ffprobe returned malformed metadata")

    streams = payload.get("streams")
    format_data = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise AcquisitionError("ffprobe metadata is missing streams or format")
    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise AcquisitionError("ffprobe found no video stream")
    audio_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )

    duration_seconds = _positive_number(format_data.get("duration"), "duration")
    width = _positive_integer(video_stream.get("width"), "video width")
    height = _positive_integer(video_stream.get("height"), "video height")
    video_codec = video_stream.get("codec_name")
    if not isinstance(video_codec, str) or not video_codec.strip():
        raise AcquisitionError("ffprobe metadata has no video codec")
    audio_codec: str | None = None
    if audio_stream is not None:
        raw_audio_codec = audio_stream.get("codec_name")
        if not isinstance(raw_audio_codec, str) or not raw_audio_codec.strip():
            raise AcquisitionError("ffprobe metadata has an invalid audio codec")
        audio_codec = raw_audio_codec.strip()
    raw_format = format_data.get("format_name")
    format_name = (
        raw_format.strip()
        if isinstance(raw_format, str) and raw_format.strip()
        else None
    )

    return MediaMetadata(
        duration_ms=max(1, round(duration_seconds * 1000)),
        width=width,
        height=height,
        video_codec=video_codec.strip(),
        audio_codec=audio_codec,
        format_name=format_name,
        size_bytes=source.stat().st_size,
    )


def chapters_from_metadata(
    metadata: dict[str, object], *, duration_ms: int
) -> tuple[Chapter, ...]:
    """Normalize yt-dlp chapter metadata into ordered half-open intervals."""

    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    raw_chapters = metadata.get("chapters")
    if raw_chapters is None:
        return ()
    if not isinstance(raw_chapters, list):
        raise AcquisitionError("yt-dlp chapters metadata is malformed")

    parsed: list[tuple[str, int, int | None]] = []
    for raw in raw_chapters:
        if not isinstance(raw, dict):
            raise AcquisitionError("yt-dlp chapter is malformed")
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise AcquisitionError("yt-dlp chapter has no title")
        start_ms = _milliseconds(raw.get("start_time"), "chapter start", allow_zero=True)
        raw_end = raw.get("end_time")
        end_ms = (
            None
            if raw_end is None
            else _milliseconds(raw_end, "chapter end", allow_zero=False)
        )
        parsed.append((title.strip(), start_ms, end_ms))

    chapters: list[Chapter] = []
    previous_start = -1
    for index, (title, start_ms, explicit_end) in enumerate(parsed):
        if start_ms <= previous_start or start_ms >= duration_ms:
            raise AcquisitionError("yt-dlp chapters are outside the media timeline")
        next_start = parsed[index + 1][1] if index + 1 < len(parsed) else duration_ms
        end_ms = explicit_end if explicit_end is not None else next_start
        # yt-dlp and ffprobe can round the same media duration differently.
        # Internal boundaries must still agree exactly; tolerate one second at
        # the final boundary and normalize it to the probed duration.
        if index + 1 < len(parsed):
            interval_is_valid = end_ms == next_start
        else:
            interval_is_valid = abs(end_ms - duration_ms) <= 1_000
            end_ms = duration_ms
        if not interval_is_valid or end_ms <= start_ms or end_ms > duration_ms:
            raise AcquisitionError("yt-dlp chapter intervals are inconsistent")
        chapters.append(Chapter(index, title, start_ms, end_ms))
        previous_start = start_ms
    return tuple(chapters)


def _execute(
    runner: CommandRunner, argv: Sequence[str], *, timeout_seconds: int
) -> CommandResult:
    try:
        return runner(tuple(argv), timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        raise AcquisitionError(f"{argv[0]} timed out") from error
    except OSError as error:
        raise AcquisitionError(f"{argv[0]} could not be started") from error


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise AcquisitionError(f"{label} was not created")
    if path.stat().st_size > MAXIMUM_METADATA_BYTES:
        raise AcquisitionError(f"{label} exceeds the configured size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AcquisitionError(f"{label} is malformed") from error
    if not isinstance(value, dict):
        raise AcquisitionError(f"{label} is malformed")
    return value


def _require_regular_file(path: Path, *, root: Path, label: str) -> None:
    if not _is_regular_file_within(path, root):
        raise AcquisitionError(f"{label} was not created safely")


def _is_regular_file_within(path: Path, root: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _positive_number(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise AcquisitionError(f"ffprobe metadata has invalid {label}") from error
    if not math.isfinite(number) or number <= 0:
        raise AcquisitionError(f"ffprobe metadata has invalid {label}")
    return number


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise AcquisitionError(f"ffprobe metadata has invalid {label}")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise AcquisitionError(f"ffprobe metadata has invalid {label}") from error
    if number <= 0:
        raise AcquisitionError(f"ffprobe metadata has invalid {label}")
    return number


def _milliseconds(value: object, label: str, *, allow_zero: bool) -> int:
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise AcquisitionError(f"yt-dlp metadata has invalid {label}") from error
    if not math.isfinite(seconds) or seconds < 0 or (not allow_zero and seconds <= 0):
        raise AcquisitionError(f"yt-dlp metadata has invalid {label}")
    return round(seconds * 1000)


def _command_failure(command: str, stderr: str) -> str:
    # Provider stderr may contain signed URLs, cookies, or local paths. It is
    # intentionally excluded from the exception that callers may record.
    del stderr
    return f"{command} failed"
