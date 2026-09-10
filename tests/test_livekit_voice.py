"""Voice admission, public utterance boundaries, and stale answer protection."""

from contextlib import nullcontext
import json
from unittest.mock import Mock, patch
from uuid import UUID

import jwt
import pytest

from api.interviews import voice_connection
from fastapi import HTTPException, Response
from interviews import store
from interviews.contracts import InterviewTurn
from interviews.livekit_voice import (
    VoiceCommand, VoiceUnavailable, create_voice_connection, saved_utterance,
    voice_metric_cost_usd,
)
from interviews.service import answer_interview
from tests.test_interviews import evaluation, question, session


OWNER = UUID("00000000-0000-4000-8000-000000000001")


def test_voice_usage_uses_configurable_metered_rates(monkeypatch):
    monkeypatch.setenv("LIVEKIT_INTERVIEW_STT_USD_PER_MINUTE", "0.006")
    monkeypatch.setenv("LIVEKIT_INTERVIEW_TTS_USD_PER_MILLION_CHARACTERS", "40")
    assert voice_metric_cost_usd({"type": "stt_metrics", "audio_duration": 30}) == pytest.approx(0.003)
    assert voice_metric_cost_usd({"type": "tts_metrics", "characters_count": 500}) == pytest.approx(0.02)
    assert voice_metric_cost_usd({"type": "llm_metrics", "input_tokens": 500}) == 0


def active_session():
    return session().model_copy(update={"turns": [InterviewTurn(turn_index=0, question=question())]})


def test_voice_disabled_without_optional_configuration(monkeypatch):
    monkeypatch.delenv("INTERVIEW_LIVEKIT_ENABLED", raising=False)
    with pytest.raises(VoiceUnavailable, match="disabled"):
        create_voice_connection(active_session(), OWNER)


def test_scoped_token_has_only_microphone_permission_and_server_dispatch(monkeypatch):
    pytest.importorskip("livekit.api")
    for key, value in {
        "INTERVIEW_LIVEKIT_ENABLED": "true", "LIVEKIT_URL": "wss://test.invalid",
        "LIVEKIT_API_KEY": "test-key", "LIVEKIT_API_SECRET": "test-secret-long-enough-for-hs256-tests",
    }.items():
        monkeypatch.setenv(key, value)
    credentials = create_voice_connection(active_session(), OWNER)
    claims = jwt.decode(credentials.participant_token, "test-secret-long-enough-for-hs256-tests", algorithms=["HS256"])
    assert claims["video"]["room"] == credentials.room_name
    assert claims["video"]["canPublishSources"] == ["microphone"]
    assert claims["video"]["canUpdateOwnMetadata"] is False
    assert not claims["video"].get("roomAdmin")
    assert claims["exp"] - claims["nbf"] == 600
    dispatch = claims["roomConfig"]["agents"][0]
    binding = json.loads(dispatch["metadata"])
    assert binding["owner_id"] == str(OWNER)
    assert binding["participant_identity"] == claims["sub"]
    assert binding["session_id"] == session().session_id
    assert create_voice_connection(active_session(), OWNER).room_name != credentials.room_name
    completed = active_session().model_copy(update={"status": "completed"})
    assert create_voice_connection(completed, OWNER).participant_token
    with pytest.raises(store.InterviewStateError):
        saved_utterance(completed, VoiceCommand(action="speak", turn_index=0))
    with pytest.raises(store.InterviewStateError):
        create_voice_connection(active_session().model_copy(update={"status": "ready"}), OWNER)


def test_admission_checks_owner_before_minting():
    import asyncio
    with patch("api.interviews.database_connection", return_value=nullcontext(Mock())), patch(
        "api.interviews.store.load_session", side_effect=store.InterviewNotFoundError(),
    ) as load, patch("api.interviews.create_voice_connection") as mint:
        with pytest.raises(HTTPException) as error:
            asyncio.run(voice_connection(UUID(session().session_id), Response(), OWNER))
        assert error.value.status_code == 404
        assert load.call_args.kwargs["owner_id"] == OWNER
        mint.assert_not_called()


def test_saved_speech_does_not_expose_private_answer_or_old_questions():
    found = active_session()
    spoken = saved_utterance(found, VoiceCommand(action="speak", turn_index=0))
    assert spoken == question().text
    assert question().suggested_answer not in spoken
    found.turns[0].answer_text = "My answer"
    found.turns[0].evaluation = evaluation(complete=True)
    found.turns.append(InterviewTurn(turn_index=1, question=question()))
    with pytest.raises(store.InterviewStateError, match="active question"):
        saved_utterance(found, VoiceCommand(action="speak", turn_index=0))
    reaction = saved_utterance(found, VoiceCommand(action="speak", turn_index=0, utterance="reaction"))
    assert "[N7" not in reaction
    assert "Grounded feedback" not in reaction
    with pytest.raises(store.InterviewStateError):
        saved_utterance(found, VoiceCommand(action="speak", turn_index=1, utterance="clarification", clarification_index=0))


def test_stale_submission_does_not_evaluate_new_question():
    found = active_session()
    found.turns[0].turn_index = 1
    with patch("interviews.service.store.load_session", return_value=found), patch("interviews.service.answer_graph") as graph:
        with pytest.raises(store.InterviewStateError, match="different interview turn"):
            answer_interview(Mock(), found.session_id, owner_id=OWNER, answer_text="Old answer", expected_turn_index=0)
        graph.invoke.assert_not_called()
