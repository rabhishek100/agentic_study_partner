"""Exercise stream concurrency and worker authority without provider calls."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import pytest

try:
    import livekit.agents
except ImportError:
    raise unittest.SkipTest("install the voice extra to test the media worker")

from livekit import rtc
from livekit.agents import stt
from interviews.livekit_voice import VoiceBinding, VoiceCommand
from interviews.voice_worker import InterviewMedia
from tests.test_livekit_voice import active_session, OWNER


def media():
    worker = InterviewMedia.__new__(InterviewMedia)
    worker.binding = VoiceBinding(
        session_id=active_session().session_id, owner_id=OWNER,
        participant_identity="candidate", room_name="room",
    )
    worker.ctx = SimpleNamespace(room=SimpleNamespace(remote_participants={"candidate": Mock()}))
    worker.sequence = 0
    worker.capture = None
    worker.playback = None
    worker.feed = None
    worker.stream = None
    worker.lock = asyncio.Lock()
    worker.speech = Mock()
    worker.speech_started = True
    worker.load_session = Mock(return_value=active_session())
    worker.emit = AsyncMock()
    return worker


def test_worker_rejects_other_participant_before_processing_command():
    worker = media()
    with pytest.raises(rtc.RpcError):
        asyncio.run(worker.command(SimpleNamespace(caller_identity="intruder", payload='{"action":"listen"}')))
    worker.load_session.assert_not_called()


def test_disconnect_cleanup_closes_providers_after_session_already_stopped():
    worker = media()
    worker.speech.interrupt.side_effect = RuntimeError("AgentSession isn't running")
    worker.speech.aclose = AsyncMock()
    worker.stt = Mock(aclose=AsyncMock())
    worker.tts = Mock(aclose=AsyncMock())
    asyncio.run(worker.close())
    worker.speech.interrupt.assert_not_called()
    worker.speech.aclose.assert_awaited_once()
    worker.stt.aclose.assert_awaited_once()
    worker.tts.aclose.assert_awaited_once()


def test_worker_rejects_arbitrary_speech_text():
    worker = media()
    with pytest.raises(rtc.RpcError):
        asyncio.run(worker.command(SimpleNamespace(
            caller_identity="candidate", payload='{"action":"speak","text":"Invented answer"}',
        )))
    worker.speech.say.assert_not_called()


def test_audio_keeps_flowing_while_transcript_delivery_is_pending():
    async def scenario():
        audio_queue = asyncio.Queue()
        text_queue = asyncio.Queue()
        pushed = []
        delivering = asyncio.Event()
        release = asyncio.Event()

        class Audio:
            closed = False
            def __aiter__(self): return self
            async def __anext__(self): return await audio_queue.get()
            async def aclose(self): self.closed = True

        class Stream:
            closed = False
            def __aiter__(self): return self
            async def __anext__(self): return await text_queue.get()
            def push_frame(self, frame): pushed.append(frame)
            async def aclose(self): self.closed = True

        audio, stream = Audio(), Stream()
        worker = media()
        worker.stt = Mock(stream=Mock(return_value=stream))

        async def emit(*args, **kwargs):
            delivering.set()
            await release.wait()

        worker.emit = AsyncMock(side_effect=emit)
        with patch("interviews.voice_worker.rtc.AudioStream.from_participant", return_value=audio):
            worker.capture = asyncio.create_task(worker.transcribe(4))
            await audio_queue.put(SimpleNamespace(frame="first"))
            await text_queue.put(SimpleNamespace(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[SimpleNamespace(text="First phrase")],
            ))
            await asyncio.wait_for(delivering.wait(), timeout=1)
            await audio_queue.put(SimpleNamespace(frame="continued speech"))
            for _ in range(5): await asyncio.sleep(0)
            assert pushed == ["first", "continued speech"]
            await worker.cancel_capture()
            assert audio.closed and stream.closed
            worker.emit.assert_called_once_with("transcript", epoch=4, sequence=1, text="First phrase")

    asyncio.run(scenario())


def test_tts_failure_does_not_report_successful_playback():
    worker = media()
    handle = Mock(wait_for_playout=AsyncMock(), exception=Mock(return_value=RuntimeError("provider failed")))
    worker.speech.say.return_value = handle
    asyncio.run(worker.speak(VoiceCommand(action="speak", request_id="speech-1")))
    assert worker.emit.call_args.args == ("speech_error",)
    worker.speech.interrupt.assert_called_once_with(force=True)
    worker.speech.say.assert_called_once_with(
        active_session().turns[0].question.text, allow_interruptions=False, add_to_chat_ctx=False,
    )
