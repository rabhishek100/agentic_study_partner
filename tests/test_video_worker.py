"""Stable failure policy for the local video worker."""

from video.audio import AudioTranscriptionError
from video.errors import VideoBudgetExceeded, VideoErrorCode
from video.evidence_store import VideoQualityGateError
from video.worker import _classify_failure


def test_audio_provider_failures_are_bounded_retries() -> None:
    assert _classify_failure(AudioTranscriptionError("provider failed")) == (
        VideoErrorCode.PROVIDER_UNAVAILABLE,
        True,
    )


def test_quality_failure_is_terminal_invalid_media() -> None:
    assert _classify_failure(VideoQualityGateError({"visual": False})) == (
        VideoErrorCode.INVALID_MEDIA,
        False,
    )


def test_budget_failure_is_terminal_and_keeps_its_specific_code() -> None:
    assert _classify_failure(VideoBudgetExceeded()) == (
        VideoErrorCode.BUDGET_EXCEEDED,
        False,
    )
