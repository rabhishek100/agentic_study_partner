"""Stable failure policy and stage routing for the video worker."""

import pytest

from video.audio import AudioTranscriptionError
from video.errors import VideoBudgetExceeded, VideoErrorCode
from video.evidence_store import VideoQualityGateError
from video.states import Stage
from video.worker import SUPPORTED_STAGES, _classify_failure, configured_supported_stages


def test_worker_supports_every_stage_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIDEO_WORKER_STAGES", raising=False)
    assert configured_supported_stages() == SUPPORTED_STAGES


def test_worker_stage_allowlist_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_WORKER_STAGES", "acquire_source, transcript")
    assert configured_supported_stages() == {
        Stage.ACQUIRE_SOURCE,
        Stage.TRANSCRIPT,
    }


def test_worker_rejects_unknown_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_WORKER_STAGES", "acquire_source, magic")
    with pytest.raises(ValueError, match="unknown stage"):
        configured_supported_stages()


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
