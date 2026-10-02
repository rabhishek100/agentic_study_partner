"""LiveKit media worker; interview reasoning remains in the existing HTTP API.

Run with ``uv run --extra voice python -m interviews.voice_worker dev``.
LiveKit Inference supplies streaming STT/TTS; no independent LLM is attached.
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
    Agent, AgentServer, AgentSession, JobContext, TurnHandlingOptions,
    cli, inference, room_io, stt,
)

from observability import record_voice_metrics, traced, annotate, flush_traces, record_estimate
from operations_telemetry import configure_voice_worker
from interviews import store
from interviews.livekit_voice import (
    AGENT_NAME, EVENT_TOPIC, RPC_METHOD, VoiceBinding, VoiceCommand,
    saved_utterance, voice_event, voice_metric_cost_usd,
)
from storage.database import connection as database_connection


load_dotenv()
logger = logging.getLogger("study_partner.interview.voice")
server = AgentServer(
    # A portfolio pilot needs on-demand jobs, not eight idle subprocesses.
    num_idle_processes=0,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8081")),
    drain_timeout=30,
)
configure_voice_worker(server, "study-partner-interview-voice")


class InterviewMedia:
    def __init__(self, ctx: JobContext, binding: VoiceBinding):
        self.ctx = ctx
        self.binding = binding
        self.lock = asyncio.Lock()
        self.capture: asyncio.Task | None = None
        self.playback: asyncio.Task | None = None
        self.feed: asyncio.Task | None = None
        self.stream: stt.SpeechStream | None = None
        self.sequence = 0
        self.speech_started = False
        self.stt = inference.STT(
            model=os.getenv("LIVEKIT_INTERVIEW_STT_MODEL", "deepgram/nova-3"),
            language="en",
        )
        voice = os.getenv("LIVEKIT_INTERVIEW_TTS_VOICE", "").strip()
        if not voice:
            raise ValueError("LIVEKIT_INTERVIEW_TTS_VOICE is required")
        self.tts = inference.TTS(
            model=os.getenv("LIVEKIT_INTERVIEW_TTS_MODEL", "cartesia/sonic-3"),
            voice=voice,
        )
        self.speech = AgentSession(
            tts=self.tts, vad=None,
            turn_handling=TurnHandlingOptions(turn_detection="manual"),
        )
        # Provider SDK metrics, never transcript/audio content. Pricing remains
        # in the LiveKit usage dashboard until the evaluation establishes cost.
        for provider in (self.stt, self.tts):
            provider.on("metrics_collected", self.record_metrics)

    @traced("interviews.voice_worker.InterviewMedia.record_metrics", flow="interview_voice", run_type="tool")
    def record_metrics(self, metrics):
        payload = metrics.model_dump(mode="json")
        record_voice_metrics(payload)
        logger.info(json.dumps({
            "event": "interview_voice_usage", "session_id": str(self.binding.session_id),
            "room": self.binding.room_name, "metrics": payload,
        }))
        cost = voice_metric_cost_usd(payload)
        record_estimate(cost)
        if cost:
            try:
                with database_connection() as connection:
                    store.add_voice_cost(
                        connection, self.binding.session_id,
                        owner_id=self.binding.owner_id, cost_usd=cost,
                    )
            except Exception:
                # Accounting must be visible, but a transient database failure
                # must not stop live media in the middle of an interview.
                logger.exception(
                    "Failed to persist interview voice cost",
                    extra={"session_id": str(self.binding.session_id)},
                )

    def load_session(self):
        with database_connection() as connection:
            return store.load_session(
                connection, self.binding.session_id, owner_id=self.binding.owner_id,
            )

    async def emit(self, kind: str, **values):
        await self.ctx.room.local_participant.publish_data(
            voice_event(kind, **values), topic=EVENT_TOPIC, reliable=True,
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

    @traced("interviews.voice_worker.InterviewMedia.transcribe", flow="interview_voice")
    async def transcribe(self, epoch: int):
        participant = self.ctx.room.remote_participants.get(self.binding.participant_identity)
        if participant is None:
            return
        audio = rtc.AudioStream.from_participant(
            participant=participant, track_source=rtc.TrackSource.SOURCE_MICROPHONE,
            sample_rate=16000, num_channels=1, capacity=100,
        )
        stream = self.stt.stream()
        self.stream = stream

        async def feed():
            async for event in audio:
                stream.push_frame(event.frame)

        self.feed = asyncio.create_task(feed())
        try:
            # A bounded session ceiling also prevents an abandoned open tab
            # streaming indefinitely. Each fresh capture rechecks DB ownership.
            async with asyncio.timeout(122 * 60):
                async for event in stream:
                    if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                        text = event.alternatives[0].text.strip() if event.alternatives else ""
                        if text:
                            self.sequence += 1
                            await self.emit("transcript", epoch=epoch, sequence=self.sequence, text=text)
                    elif event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                        await self.emit("hearing", epoch=epoch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Interview streaming transcription failed", extra={"session_id": str(self.binding.session_id)})
            await self.emit("capture_error", epoch=epoch, message="Voice transcription stopped. Reconnect voice or type your answer.")
        finally:
            self.feed.cancel()
            with suppress(asyncio.CancelledError):
                await self.feed
            await audio.aclose()
            await stream.aclose()
            self.stream = None
            self.feed = None

    @traced("interviews.voice_worker.InterviewMedia.speak", flow="interview_voice")
    async def speak(self, command: VoiceCommand):
        started_tasks: list[asyncio.Task] = []

        def state_changed(event):
            if event.new_state == "speaking":
                started_tasks.append(asyncio.create_task(self.emit("speech_started", request_id=command.request_id)))

        self.speech.on("agent_state_changed", state_changed)
        try:
            session = await asyncio.to_thread(self.load_session)
            text = saved_utterance(session, command)
            async with asyncio.timeout(60):
                handle = self.speech.say(text, allow_interruptions=False, add_to_chat_ctx=False)
                await handle.wait_for_playout()
                if handle.exception() is not None:
                    raise RuntimeError("interviewer speech generation failed")
            await self.emit("speech_done", request_id=command.request_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Interview streaming speech failed", extra={"session_id": str(self.binding.session_id)})
            self.speech.interrupt(force=True)
            await self.emit("speech_error", request_id=command.request_id, message="Interviewer speech is unavailable. Read the response on screen.")
        finally:
            self.speech.off("agent_state_changed", state_changed)
            for task in started_tasks:
                task.cancel()
            if started_tasks:
                await asyncio.gather(*started_tasks, return_exceptions=True)

    @traced("interviews.voice_worker.InterviewMedia.command", flow="interview_voice")
    async def command(self, data: rtc.RpcInvocationData) -> str:
        if data.caller_identity != self.binding.participant_identity:
            raise rtc.RpcError(1403, "Voice participant does not match this session")
        try:
            command = VoiceCommand.model_validate_json(data.payload)
            async with self.lock:
                if command.action == "stop_speaking":
                    await self.cancel_speech()
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
                            await asyncio.wait_for(asyncio.shield(self.capture), timeout=5)
                        except asyncio.TimeoutError:
                            # Final text may precede provider stream closure.
                            # Bounded drain expiry is normal cleanup, not a
                            # failed RPC or a discarded transcript.
                            pass
                        finally:
                            await self.cancel_capture()
                elif command.action == "listen":
                    session = await asyncio.to_thread(self.load_session)
                    pending = next((t for t in reversed(session.turns) if t.answer_text is None), None)
                    if session.status != "active" or pending is None or pending.turn_index != command.turn_index:
                        raise store.InterviewStateError("voice capture requires the current active turn")
                    await self.cancel_capture()
                    self.capture = asyncio.create_task(self.transcribe(command.epoch))
                elif command.action == "speak":
                    await self.cancel_capture()
                    await self.cancel_speech()
                    self.playback = asyncio.create_task(self.speak(command))
            return "ok"
        except (ValueError, store.InterviewStateError) as error:
            raise rtc.RpcError(1400, str(error)) from error

    async def close(self):
        # Participant disconnect can close AgentSession before job cleanup.
        # aclose handles running and closed sessions; interrupt requires a live one.
        self.speech_started = False
        await self.cancel_capture()
        await self.cancel_speech()
        await self.speech.aclose()
        await self.stt.aclose()
        await self.tts.aclose()
        await asyncio.to_thread(flush_traces)


@server.rtc_session(agent_name=AGENT_NAME)
@traced("interviews.voice_worker.interview_voice", flow="interview_voice")
async def interview_voice(ctx: JobContext):
    binding = VoiceBinding.model_validate_json(ctx.job.metadata)
    annotate(session_id=str(binding.session_id), thread_id=str(binding.session_id))
    if ctx.room.name != binding.room_name:
        raise ValueError("voice dispatch room mismatch")
    media = InterviewMedia(ctx, binding)
    await asyncio.to_thread(media.load_session)
    ctx.add_shutdown_callback(media.close)
    await ctx.connect()
    await media.speech.start(
        agent=Agent(instructions="Speak only explicitly supplied interview text."),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=binding.participant_identity,
            audio_input=False, text_input=False, video_input=False,
            text_output=False,
        ),
        record=False,
    )
    media.speech_started = True
    ctx.room.local_participant.register_rpc_method(RPC_METHOD, media.command)
    await ctx.room.local_participant.set_attributes({"interview.voice.ready": "true"})


if __name__ == "__main__":
    cli.run_app(server)
