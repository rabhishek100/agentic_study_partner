"""Admission and speech authority for interruptible read-aloud."""

from contextlib import nullcontext
import json
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import jwt
import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from api.narration import narration_voice_connection

from narration.livekit_voice import (
    VoiceBinding,
    VoiceCommand,
    VoiceUnavailable,
    create_voice_connection,
    spoken_saved_answer,
)


OWNER = UUID("00000000-0000-4000-8000-000000000001")
CONVERSATION = UUID("00000000-0000-4000-8000-000000000002")


def test_narration_voice_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NARRATION_LIVEKIT_ENABLED", raising=False)
    with pytest.raises(VoiceUnavailable, match="disabled"):
        create_voice_connection(CONVERSATION, OWNER)


def test_api_checks_conversation_ownership_before_minting():
    import asyncio

    with patch(
        "api.narration.database_connection", return_value=nullcontext(Mock())
    ), patch("api.narration.load_conversation", return_value=None), patch(
        "api.narration.create_voice_connection"
    ) as mint:
        with pytest.raises(HTTPException) as error:
            asyncio.run(narration_voice_connection(CONVERSATION, OWNER))
        assert error.value.status_code == 404
        mint.assert_not_called()


def test_narration_token_is_microphone_only_and_owner_bound(monkeypatch):
    pytest.importorskip("livekit.api")
    secret = "test-secret-long-enough-for-hs256-tests"
    for key, value in {
        "NARRATION_LIVEKIT_ENABLED": "true",
        "LIVEKIT_URL": "wss://test.invalid",
        "LIVEKIT_API_KEY": "test-key",
        "LIVEKIT_API_SECRET": secret,
    }.items():
        monkeypatch.setenv(key, value)
    credentials = create_voice_connection(CONVERSATION, OWNER)
    claims = jwt.decode(credentials.participant_token, secret, algorithms=["HS256"])
    assert claims["video"]["room"] == credentials.room_name
    assert claims["video"]["canPublishSources"] == ["microphone"]
    assert claims["video"]["canUpdateOwnMetadata"] is False
    assert not claims["video"].get("roomAdmin")
    binding = json.loads(claims["roomConfig"]["agents"][0]["metadata"])
    assert binding["owner_id"] == str(OWNER)
    assert binding["conversation_id"] == str(CONVERSATION)


def test_voice_command_cannot_carry_arbitrary_speech():
    with pytest.raises(ValidationError):
        VoiceCommand.model_validate(
            {"action": "speak", "text": "An answer that was never grounded"}
        )


def test_saved_answer_cleanup_keeps_claims_and_removes_syntax():
    spoken = spoken_saved_answer(
        "## Result\n**Regularization** reduces variance [S1:p12]. "
        "See [the derivation](https://invalid.example)."
    )
    assert spoken == "Result Regularization reduces variance. See the derivation."


def test_worker_speaks_only_a_child_thread_of_the_bound_conversation():
    pytest.importorskip("livekit.agents")
    from narration.voice_worker import NarrationMedia

    worker = NarrationMedia.__new__(NarrationMedia)
    worker.binding = VoiceBinding(
        conversation_id=CONVERSATION,
        owner_id=OWNER,
        participant_identity="reader",
        room_name="room",
    )
    command = VoiceCommand(
        action="speak", side_chat_id=uuid4(), turn_index=0
    )
    wrong_parent = {
        "parent_conversation_id": uuid4(),
    }
    with patch(
        "narration.voice_worker.database_connection",
        return_value=nullcontext(Mock()),
    ), patch(
        "narration.voice_worker.load_conversation", return_value=wrong_parent
    ), patch("narration.voice_worker.load_turns") as turns:
        with pytest.raises(ValueError, match="does not belong"):
            worker.saved_answer(command)
        turns.assert_not_called()


def test_worker_loads_and_cleans_the_persisted_grounded_answer():
    pytest.importorskip("livekit.agents")
    from narration.voice_worker import NarrationMedia

    worker = NarrationMedia.__new__(NarrationMedia)
    worker.binding = VoiceBinding(
        conversation_id=CONVERSATION,
        owner_id=OWNER,
        participant_identity="reader",
        room_name="room",
    )
    side_chat_id = uuid4()
    command = VoiceCommand(
        action="speak", side_chat_id=side_chat_id, turn_index=2
    )
    child = {"parent_conversation_id": CONVERSATION}
    with patch(
        "narration.voice_worker.database_connection",
        return_value=nullcontext(Mock()),
    ), patch(
        "narration.voice_worker.load_conversation", return_value=child
    ) as load, patch(
        "narration.voice_worker.load_turns",
        return_value=[{"turn_index": 2, "answer": "**Grounded** result [S1]."}],
    ):
        assert worker.saved_answer(command) == "Grounded result."
        assert load.call_args.kwargs["owner_id"] == OWNER
