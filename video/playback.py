"""Short-lived signed links so a browser can play a stored lecture.

A `<video>` element cannot carry an Authorization header, so the canonical
object needs a URL that authenticates itself. The link is signed for one video
and one owner, expires quickly, and grants nothing else: it is a read capability
for bytes the owner already owns, not a session.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
import hashlib
import hmac
import os
import time
from uuid import UUID


DEFAULT_TTL_SECONDS = 6 * 60 * 60
# Expiries land on a fixed grid so repeated requests produce the identical
# URL. A freshly minted token per response changed the player's src on every
# poll, which reloaded the video and threw away the viewer's position.
EXPIRY_BUCKET_SECONDS = 30 * 60


class PlaybackTokenError(ValueError):
    """The link is missing, malformed, expired, or not for this video."""


def _secret() -> bytes:
    configured = (
        os.getenv("VIDEO_PLAYBACK_SECRET")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    ).strip()
    if not configured:
        raise PlaybackTokenError("no signing secret is configured for playback")
    return configured.encode()


def sign_playback(
    *, video_id: str | UUID, owner_id: str | UUID, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    ttl = max(60, ttl_seconds)
    bucket = min(EXPIRY_BUCKET_SECONDS, ttl)
    # Round up to the next boundary: stable between calls, and never issued
    # with less than the requested lifetime remaining.
    expires_at = ((int(time.time()) + ttl) // bucket + 1) * bucket
    payload = f"{UUID(str(video_id))}:{UUID(str(owner_id))}:{expires_at}"
    digest = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return (
        urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        + "."
        + urlsafe_b64encode(digest).decode().rstrip("=")
    )


def verify_playback(token: str) -> tuple[UUID, UUID]:
    """Return the video and owner a valid, unexpired token authorizes."""

    raw = (token or "").strip()
    if raw.count(".") != 1:
        raise PlaybackTokenError("malformed playback token")
    encoded_payload, encoded_digest = raw.split(".")
    try:
        payload = urlsafe_b64decode(_pad(encoded_payload)).decode()
        digest = urlsafe_b64decode(_pad(encoded_digest))
    except (ValueError, UnicodeDecodeError) as error:
        raise PlaybackTokenError("malformed playback token") from error
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(digest, expected):
        raise PlaybackTokenError("playback token signature does not match")
    parts = payload.split(":")
    if len(parts) != 3:
        raise PlaybackTokenError("malformed playback token")
    video, owner, expires_at = parts
    if int(expires_at) < int(time.time()):
        raise PlaybackTokenError("playback link has expired")
    try:
        return UUID(video), UUID(owner)
    except ValueError as error:
        raise PlaybackTokenError("malformed playback token") from error


def playback_url(*, video_id: str | UUID, owner_id: str | UUID) -> str | None:
    """A relative, signed stream URL, or None when signing is unavailable."""

    try:
        token = sign_playback(video_id=video_id, owner_id=owner_id)
    except PlaybackTokenError:
        return None
    return f"/api/videos/{UUID(str(video_id))}/stream?token={token}"


def _pad(value: str) -> str:
    return value + "=" * (-len(value) % 4)
