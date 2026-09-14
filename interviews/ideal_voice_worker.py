"""LiveKit worker that performs a persisted ideal interview with two voices."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import os

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, TurnHandlingOptions, cli, inference, room_io

from interviews import ideal_store
from interviews.ideal_livekit import (
    AGENT_NAME,
    EVENT_TOPIC,
    RPC_METHOD,
    IdealVoiceBinding,
    IdealVoiceCommand,
    pronunciation_text,
    voice_event,
)
from interviews.livekit_voice import voice_metric_cost_usd
from storage.database import connection as database_connection


load_dotenv()
logger = logging.getLogger("study_partner.ideal_interview.voice")
server = AgentServer(
    num_idle_processes=0,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8082")),
    drain_timeout=30,
)


class IdealInterviewMedia:
    def __init__(self, ctx: JobContext, binding: IdealVoiceBinding):
        self.ctx = ctx
        self.binding = binding
        self.playback: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        voice = os.getenv("LIVEKIT_IDEAL_INTERVIEWER_VOICE", "").strip()
        if not voice:
            raise ValueError("LIVEKIT_IDEAL_INTERVIEWER_VOICE is required")
        self.interviewer_voice = voice
        self.candidate_voice = os.getenv("LIVEKIT_IDEAL_CANDIDATE_VOICE", "").strip()
        if not self.candidate_voice:
            raise ValueError("LIVEKIT_IDEAL_CANDIDATE_VOICE is required")
        self.tts = inference.TTS(
            model=os.getenv("LIVEKIT_IDEAL_TTS_MODEL", "cartesia/sonic-3.6"),
            voice=self.interviewer_voice,
            extra_kwargs={"speed": float(os.getenv("LIVEKIT_IDEAL_TTS_SPEED", "0.96"))},
        )
        self.speech = AgentSession(
            tts=self.tts,
            vad=None,
            turn_handling=TurnHandlingOptions(turn_detection="manual"),
            use_tts_aligned_transcript=True,
        )
        self.tts.on("metrics_collected", self.record_metrics)

    def load_flow(self):
        with database_connection() as connection:
            return ideal_store.load_flow(
                connection, self.binding.flow_id, owner_id=self.binding.owner_id
            )

    def record_metrics(self, metrics):
        payload = metrics.model_dump(mode="json")
        logger.info(json.dumps({
            "event": "ideal_interview_voice_usage",
            "flow_id": str(self.binding.flow_id),
            "room": self.binding.room_name,
            "metrics": payload,
        }))
        cost = voice_metric_cost_usd(payload)
        if cost:
            try:
                with database_connection() as connection:
                    ideal_store.add_voice_cost(
                        connection,
                        self.binding.flow_id,
                        owner_id=self.binding.owner_id,
                        cost_usd=cost,
                    )
            except Exception:
                logger.exception("Failed to persist ideal interview voice cost")

    async def emit(self, kind: str, **values):
        await self.ctx.room.local_participant.publish_data(
            voice_event(kind, **values),
            topic=EVENT_TOPIC,
            reliable=True,
            destination_identities=[self.binding.participant_identity],
        )

    async def stop(self):
        if self.playback:
            task = self.playback
            self.playback = None
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        with suppress(RuntimeError):
            await self.speech.interrupt(force=True)

    async def say(self, *, speaker: str, text: str, exchange_index: int):
        voice = self.interviewer_voice if speaker == "interviewer" else self.candidate_voice
        self.tts.update_options(voice=voice)
        await self.emit("speaker_started", speaker=speaker, exchange_index=exchange_index)
        handle = self.speech.say(
            pronunciation_text(text),
            allow_interruptions=False,
            add_to_chat_ctx=False,
        )
        await handle.wait_for_playout()
        if handle.exception() is not None:
            raise RuntimeError("ideal interview speech generation failed")
        await self.emit("speaker_done", speaker=speaker, exchange_index=exchange_index)

    async def play(self, command: IdealVoiceCommand):
        try:
            flow = await asyncio.to_thread(self.load_flow)
            if command.start_exchange >= len(flow.exchanges):
                raise ValueError("playback start is outside the transcript")
            for exchange in flow.exchanges[command.start_exchange:]:
                if not (
                    exchange.exchange_index == command.start_exchange
                    and command.start_speaker == "candidate"
                ):
                    await self.say(
                        speaker="interviewer",
                        text=exchange.interviewer_text,
                        exchange_index=exchange.exchange_index,
                    )
                    await asyncio.sleep(exchange.pause_after_question_ms / 1_000)
                await self.say(
                    speaker="candidate",
                    text=exchange.candidate_text,
                    exchange_index=exchange.exchange_index,
                )
                await asyncio.sleep(exchange.pause_after_answer_ms / 1_000)
            await self.emit("playback_done", request_id=command.request_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Ideal interview playback failed")
            await self.emit(
                "playback_error",
                request_id=command.request_id,
                message="Playback stopped. Try again from the current exchange.",
            )

    async def command(self, data: rtc.RpcInvocationData) -> str:
        if data.caller_identity != self.binding.participant_identity:
            raise rtc.RpcError(1403, "Voice participant does not match this flow")
        try:
            command = IdealVoiceCommand.model_validate_json(data.payload)
            async with self.lock:
                await self.stop()
                if command.action == "play":
                    self.playback = asyncio.create_task(self.play(command))
            return "ok"
        except ValueError as error:
            raise rtc.RpcError(1400, str(error)) from error

    async def close(self):
        await self.stop()
        await self.speech.aclose()
        await self.tts.aclose()


@server.rtc_session(agent_name=AGENT_NAME)
async def ideal_interview_voice(ctx: JobContext):
    binding = IdealVoiceBinding.model_validate_json(ctx.job.metadata)
    if ctx.room.name != binding.room_name:
        raise ValueError("ideal interview voice dispatch room mismatch")
    media = IdealInterviewMedia(ctx, binding)
    await asyncio.to_thread(media.load_flow)
    ctx.add_shutdown_callback(media.close)
    await ctx.connect()
    await media.speech.start(
        agent=Agent(instructions="Speak only the persisted ideal interview transcript."),
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
    ctx.room.local_participant.register_rpc_method(RPC_METHOD, media.command)
    await ctx.room.local_participant.set_attributes({"ideal.interview.voice.ready": "true"})


if __name__ == "__main__":
    cli.run_app(server)
