"""Deterministic source validation; network acquisition belongs to the worker."""

from dataclasses import dataclass
from pathlib import PurePath
import re
from urllib.parse import parse_qs, urlparse


YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
MAXIMUM_FILENAME_LENGTH = 255


class InvalidVideoSource(ValueError):
    """The source cannot identify one supported video."""


@dataclass(frozen=True)
class YouTubeSource:
    video_id: str
    canonical_url: str


def parse_youtube_url(value: str) -> YouTubeSource:
    """Return one canonical YouTube identity without making a network call."""

    raw = value.strip()
    try:
        parsed = urlparse(raw)
    except ValueError as error:
        raise InvalidVideoSource("invalid YouTube URL") from error
    if parsed.scheme not in {"http", "https"}:
        raise InvalidVideoSource("YouTube URL must use http or https")

    host = (parsed.hostname or "").lower()
    path = [part for part in parsed.path.split("/") if part]
    identifier: str | None = None
    if host in SHORT_HOSTS:
        identifier = path[0] if path else None
    elif host in YOUTUBE_HOSTS:
        if parsed.path.rstrip("/") == "/watch":
            identifier = parse_qs(parsed.query).get("v", [None])[0]
        elif len(path) >= 2 and path[0] in {"embed", "shorts", "live"}:
            identifier = path[1]
    else:
        raise InvalidVideoSource("URL is not hosted by YouTube")

    if identifier is None or not YOUTUBE_ID.fullmatch(identifier):
        raise InvalidVideoSource("URL must identify one YouTube video")
    return YouTubeSource(
        video_id=identifier,
        canonical_url=f"https://www.youtube.com/watch?v={identifier}",
    )


def display_filename(value: str) -> str:
    """Clean upload display metadata without turning it into a storage path."""

    cleaned = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(character for character in cleaned if character.isprintable())
    if not cleaned:
        raise InvalidVideoSource("video filename cannot be blank")
    return cleaned[:MAXIMUM_FILENAME_LENGTH]


def upload_extension(filename: str, media_type: str) -> str:
    if media_type not in ALLOWED_VIDEO_TYPES:
        raise InvalidVideoSource(f"unsupported video content type: {media_type}")
    suffix = PurePath(filename).suffix.lower()
    expected = {
        "video/mp4": {".mp4"},
        "video/webm": {".webm"},
        "video/quicktime": {".mov", ".qt"},
    }[media_type]
    if suffix not in expected:
        raise InvalidVideoSource("filename extension does not match content type")
    return suffix
