"""Safe, stable failures for the video-ingestion domain."""

from enum import StrEnum
import re


# Signed URLs, bearer-ish tokens, and absolute paths appear freely in provider
# output and tool stderr, and must not reach a durable job record or a log.
_SECRET = re.compile(
    r"https?://\S+"
    r"|\b[A-Za-z0-9_-]{32,}\b"
    r"|(?<![\w.])/(?:[\w.-]+/){2,}[\w.-]*"
)


def redact(text: str, *, limit: int = 300) -> str:
    """Keep a provider's own words without keeping its secrets."""

    return " ".join(_SECRET.sub("[redacted]", text or "").split())[:limit]


class VideoErrorCode(StrEnum):
    LEASE_EXPIRED = "lease_expired"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    BUDGET_EXCEEDED = "budget_exceeded"
    INVALID_MEDIA = "invalid_media"
    SOURCE_UNAVAILABLE = "source_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNEXPECTED_ERROR = "unexpected_error"


SAFE_MESSAGES = {
    VideoErrorCode.LEASE_EXPIRED: "Video processing stalled and will resume.",
    VideoErrorCode.ATTEMPTS_EXHAUSTED: "Video processing failed repeatedly.",
    VideoErrorCode.BUDGET_EXCEEDED: "Video processing reached its cost limit.",
    VideoErrorCode.INVALID_MEDIA: "The source is not a playable video.",
    VideoErrorCode.SOURCE_UNAVAILABLE: "The video source is unavailable.",
    VideoErrorCode.PROVIDER_TIMEOUT: "A video analysis provider timed out.",
    VideoErrorCode.PROVIDER_UNAVAILABLE: "A video analysis provider is unavailable.",
    VideoErrorCode.UNEXPECTED_ERROR: "Video processing failed unexpectedly.",
}


class VideoIngestionError(RuntimeError):
    def __init__(self, code: VideoErrorCode, *, detail: str | None = None) -> None:
        self.code = VideoErrorCode(code)
        self.detail = detail
        super().__init__(detail or self.code.value)

    @property
    def safe_message(self) -> str:
        return SAFE_MESSAGES[self.code]


class VideoBudgetExceeded(VideoIngestionError):
    def __init__(self) -> None:
        super().__init__(VideoErrorCode.BUDGET_EXCEEDED)
