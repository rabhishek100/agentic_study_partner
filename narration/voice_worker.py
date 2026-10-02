"""LiveKit speech transport for interruptible read-aloud questions.

The worker transcribes the reader and speaks only answers already persisted by
the grounded side-chat workflow. It owns no retrieval or reasoning logic.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import os

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
    room_io,
    stt,
)

from observability import record_voice_metrics, traced, annotate, flush_traces
from operations_telemetry import configure_voice_worker
from narration.livekit_voice import (
    AGENT_NAME,
    EVENT_TOPIC,
    RPC_METHOD,
    VoiceBinding,
    VoiceCommand,
    spoken_saved_answer,
    voice_event,
)
from storage.conversations import load_conversation, load_turns
from storage.database import connection as database_connection


load_dotenv()
logger = logging.getLogger("study_partner.narration.voice")
server = AgentServer(
    num_idle_processes=0,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8081")),
    drain_timeout=30,
)
configure_voice_worker(server, "study-partner-narration-voice")


class NarrationMedia:
    def __init__(self, ctx: JobContext, binding: VoiceBinding):
        self.ctx = ctx
        self.binding = binding
        self.lock = asyncio.Lock()
        self.capture: asyncio.Task | None = None
        self.feed: asyncio.Task | None = None
        self.stream: stt.SpeechStream | None = None
        self.playback: asyncio.Task | None = None
        self.sequence = 0
        self.speech_started = False
        self.stt = inference.STT(
            model=os.getenv(
                "LIVEKIT_NARRATION_STT_MODEL",
                os.getenv("LIVEKIT_INTERVIEW_STT_MODEL", "deepgram/nova-3"),
            ),
            language="en",
        )
        voice = os.getenv(
            "LIVEKIT_NARRATION_TTS_VOICE",
            os.getenv("LIVEKIT_INTERVIEW_TTS_VOICE", ""),
        ).strip()
        if not voice:
            raise ValueError("LIVEKIT_NARRATION_TTS_VOICE is required")
        self.tts = inference.TTS(
            model=os.getenv(
                "LIVEKIT_NARRATION_TTS_MODEL",
                os.getenv("LIVEKIT_INTERVIEW_TTS_MODEL", "cartesia/sonic-3"),
            ),
            voice=voice,
        )
        self.speech = AgentSession(
            tts=self.tts,
            vad=None,
            turn_handling=TurnHandlingOptions(turn_detection="manual"),
        )
        for provider in (self.stt, self.tts):
            provider.on("metrics_collected", self.record_metrics)

    @traced("narration.voice_worker.NarrationMedia.record_metrics", flow="narration_voice", run_type="tool")
    def record_metrics(self, metrics):
        payload = metrics.model_dump(mode="json")
        record_voice_metrics(payload)
        logger.info(
            json.dumps(
                {
                    "event": "narration_voice_usage",
                    "conversation_id": str(self.binding.conversation_id),
                    "room": self.binding.room_name,
                    "metrics": payload,
                }
            )
        )

    def require_conversation(self):
        with database_connection(readonly=True) as connection:
            conversation = load_conversation(
                connection,
                self.binding.conversation_id,
                owner_id=self.binding.owner_id,
            )
        if conversation is None:
            raise ValueError("conversation not found")
        return conversation

    def saved_answer(self, command: VoiceCommand) -> str:
        if command.side_chat_id is None or command.turn_index is None:
            raise ValueError("a saved side-chat answer is required")
        with database_connection(readonly=True) as connection:
            side_chat = load_conversation(
                connection, command.side_chat_id, owner_id=self.binding.owner_id
            )
            if (
                side_chat is None
                or side_chat["parent_conversation_id"] != self.binding.conversation_id
            ):
                raise ValueError("side chat does not belong to this conversation")
            turn = next(
                (
                    item
                    for item in load_turns(
                        connection,
                        command.side_chat_id,
                        owner_id=self.binding.owner_id,
                    )
                    if item["turn_index"] == command.turn_index
                ),
                None,
            )
        answer = turn["answer"] if turn is not None else None
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("saved voice answer not found")
        return spoken_saved_answer(answer)

    async def emit(self, kind: str, **values):
        await self.ctx.room.local_participant.publish_data(
            voice_event(kind, **values),
            topic=EVENT_TOPIC,
            reliable=True,
            destination_identities=[self.binding.participant_identity],
        )

    async def cancel_capture(self):
        if self.capture:
            self.capture.cancel()
            with suppress(asyncio.CancelledError):
                await self.capture
            self.capture = None

    async def cancel_speech(self):
        if self.playback:
            self.playback.cancel()
            with suppress(asyncio.CancelledError):
                await self.playback
            self.playback = None
        if self.speech_started:
            await self.speech.interrupt(force=True)

    @traced("narration.voice_worker.NarrationMedia.transcribe", flow="narration_voice")
    async def transcribe(self, epoch: int):
        participant = self.ctx.room.remote_participants.get(
            self.binding.participant_identity
        )
        if participant is None:
            return
        audio = rtc.AudioStream.from_participant(
            participant=participant,
            track_source=rtc.TrackSource.SOURCE_MICROPHONE,
            sample_rate=16000,
            num_channels=1,
            capacity=100,
        )
        stream = self.stt.stream()
        self.stream = stream

        async def feed():
            async for event in audio:
                stream.push_frame(event.frame)

        self.feed = asyncio.create_task(feed())
        try:
            async with asyncio.timeout(30 * 60):
                async for event in stream:
                    if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                        text = (
                            event.alternatives[0].text.strip()
                            if event.alternatives
                            else ""
                        )
                        if text:
                            self.sequence += 1
                            await self.emit(
                                "transcript",
                                epoch=epoch,
                                sequence=self.sequence,
                                text=text,
                            )
                    elif event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                        await self.emit("hearing", epoch=epoch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Narration voice transcription failed")
            await self.emit(
                "capture_error",
                epoch=epoch,
                message="Voice transcription stopped. You can keep reading or try again.",
            )
        finally:
            if self.feed:
                self.feed.cancel()
                with suppress(asyncio.CancelledError):
                    await self.feed
            await audio.aclose()
            await stream.aclose()
            self.stream = None
            self.feed = None

    @traced("narration.voice_worker.NarrationMedia.speak", flow="narration_voice")
    async def speak(self, command: VoiceCommand):
        started: list[asyncio.Task] = []

        def state_changed(event):
            if event.new_state == "speaking":
                started.append(
                    asyncio.create_task(
                        self.emit("speech_started", request_id=command.request_id)
                    )
                )

        self.speech.on("agent_state_changed", state_changed)
        try:
            text = await asyncio.to_thread(self.saved_answer, command)
            async with asyncio.timeout(120):
                handle = self.speech.say(
                    text, allow_interruptions=False, add_to_chat_ctx=False
                )
                await handle.wait_for_playout()
                if handle.exception() is not None:
                    raise RuntimeError("voice answer playback failed")
            await self.emit("speech_done", request_id=command.request_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Narration voice answer failed")
            self.speech.interrupt(force=True)
            await self.emit(
                "speech_error",
                request_id=command.request_id,
                message="The voice answer is unavailable. The grounded answer is still in the side chat.",
            )
        finally:
            self.speech.off("agent_state_changed", state_changed)
            for task in started:
                task.cancel()
            if started:
                await asyncio.gather(*started, return_exceptions=True)

    @traced("narration.voice_worker.NarrationMedia.command", flow="narration_voice")
    async def command(self, data: rtc.RpcInvocationData) -> str:
        if data.caller_identity != self.binding.participant_identity:
            raise rtc.RpcError(1403, "Voice participant does not match this conversation")
        try:
            command = VoiceCommand.model_validate_json(data.payload)
            async with self.lock:
                if command.action == "listen":
                    await asyncio.to_thread(self.require_conversation)
                    await self.cancel_speech()
                    await self.cancel_capture()
                    self.capture = asyncio.create_task(self.transcribe(command.epoch))
                elif command.action == "stop_listening":
                    await self.cancel_capture()
                elif command.action == "flush":
                    if self.stream and self.capture:
                        if self.feed:
                            self.feed.cancel()
                            with suppress(asyncio.CancelledError):
                                await self.feed
                        self.stream.end_input()
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(self.capture), timeout=5
                            )
                        finally:
                            await self.cancel_capture()
                elif command.action == "speak":
                    await self.cancel_capture()
                    await self.cancel_speech()
                    self.playback = asyncio.create_task(self.speak(command))
                elif command.action == "stop_speaking":
                    await self.cancel_speech()
            return "ok"
        except ValueError as error:
            raise rtc.RpcError(1400, str(error)) from error

    async def close(self):
        self.speech_started = False
        await self.cancel_capture()
        await self.cancel_speech()
        await self.speech.aclose()
        await self.stt.aclose()
        await self.tts.aclose()
        await asyncio.to_thread(flush_traces)


@server.rtc_session(agent_name=AGENT_NAME)
@traced("narration.voice_worker.narration_voice", flow="narration_voice")
async def narration_voice(ctx: JobContext):
    binding = VoiceBinding.model_validate_json(ctx.job.metadata)
    annotate(conversation_id=str(binding.conversation_id), thread_id=str(binding.conversation_id))
    if ctx.room.name != binding.room_name:
        raise ValueError("voice dispatch room mismatch")
    media = NarrationMedia(ctx, binding)
    await asyncio.to_thread(media.require_conversation)
    ctx.add_shutdown_callback(media.close)
    await ctx.connect()
    await media.speech.start(
        agent=Agent(instructions="Speak only persisted, source-grounded answers."),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=binding.participant_identity,
            audio_input=False,
            text_input=False,
            video_input=False,
            text_output=False,
        ),
        record=False,
    )
    media.speech_started = True
    ctx.room.local_participant.register_rpc_method(RPC_METHOD, media.command)
    await ctx.room.local_participant.set_attributes({"narration.voice.ready": "true"})


if __name__ == "__main__":
    cli.run_app(server)
