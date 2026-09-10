"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ParticipantKind, Room, RoomEvent, Track } from "livekit-client";

import { useMicrophones } from "@/hooks/use-microphones";
import type { InterviewVoiceMode, InterviewVoiceStatus } from "@/hooks/use-interview-voice";
import { apiFetch } from "@/lib/api";
import { acceptsTranscript, parseVoiceEvent, VOICE_TOPIC } from "@/lib/livekit-interview";

type Connection = { server_url: string; participant_token: string };
type SpeechRequest = {
  id: string;
  resolve: () => void;
  timer: ReturnType<typeof setTimeout>;
};

/** Media adapter only: finalized text edits a draft; HTTP still submits answers. */
export function useLivekitInterview({ sessionId, turnIndex, onTranscript, enabled = true }: {
  sessionId: string;
  turnIndex: number;
  onTranscript: (text: string) => void;
  enabled?: boolean;
}) {
  const microphones = useMicrophones();
  const [mode, setModeState] = useState<InterviewVoiceMode>("automatic");
  const [status, setStatus] = useState<InterviewVoiceStatus>("idle");
  const [error, setError] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [loading, setLoading] = useState(false);
  const [inputLevel, setInputLevel] = useState(0);
  const roomRef = useRef<Room | null>(null);
  const connectingRef = useRef<Promise<Room> | null>(null);
  const disposedRef = useRef(false);
  const connectionEpochRef = useRef(0);
  const captureEpochRef = useRef(0);
  const lastSequenceRef = useRef(0);
  const pushHeldRef = useRef(false);
  const pushActiveRef = useRef(false);
  const requestRef = useRef<SpeechRequest | null>(null);
  const commandQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const valuesRef = useRef({ microphones, mode, turnIndex, onTranscript });
  valuesRef.current = { microphones, mode, turnIndex, onTranscript };

  const settleSpeech = useCallback(() => {
    const request = requestRef.current;
    requestRef.current = null;
    if (request) { clearTimeout(request.timer); request.resolve(); }
    setSpeaking(false);
    setLoading(false);
  }, []);

  const connect = useCallback(async (): Promise<Room> => {
    if (roomRef.current?.state === "connected") return roomRef.current;
    if (connectingRef.current) return connectingRef.current;
    const connectionEpoch = connectionEpochRef.current;
    const task = (async () => {
      const credentials = await apiFetch<Connection>(`/interviews/${sessionId}/voice-connection`, { method: "POST" });
      if (disposedRef.current || connectionEpoch !== connectionEpochRef.current) throw new Error("Voice connection cancelled");
      const room = new Room({ adaptiveStream: true });
      roomRef.current = room;
      lastSequenceRef.current = 0;
      const elements = new Set<HTMLMediaElement>();
      room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
        if (track.kind !== Track.Kind.Audio || participant.kind !== ParticipantKind.AGENT) return;
        const element = track.attach();
        elements.add(element);
        element.hidden = true;
        document.body.appendChild(element);
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((element) => { elements.delete(element); element.remove(); });
      });
      room.on(RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
        if (topic !== VOICE_TOPIC || participant?.kind !== ParticipantKind.AGENT || roomRef.current !== room) return;
        const event = parseVoiceEvent(payload);
        if (!event) return;
        if (acceptsTranscript(event, captureEpochRef.current, lastSequenceRef.current)) {
          lastSequenceRef.current = event.sequence!;
          valuesRef.current.onTranscript(event.text!);
          setStatus("listening");
        } else if (event.epoch === captureEpochRef.current && event.type === "hearing") {
          setStatus("recording");
        } else if (event.epoch === captureEpochRef.current && event.type === "capture_error") {
          setError(event.message || "Voice transcription stopped. Type your answer.");
          setStatus("idle");
          void room.localParticipant.setMicrophoneEnabled(false);
        } else if (event.request_id === requestRef.current?.id) {
          if (event.type === "speech_started") { setLoading(false); setSpeaking(true); }
          if (event.type === "speech_error") setError(event.message || "Interviewer voice is unavailable.");
          if (["speech_done", "speech_error"].includes(event.type)) settleSpeech();
        }
      });
      room.on(RoomEvent.Reconnecting, () => {
        room.remoteParticipants.forEach((p) => p.setVolume(0));
        captureEpochRef.current += 1;
        setStatus("idle");
        setError("Voice connection interrupted. When connected, select Start listening or type your answer.");
        settleSpeech();
      });
      room.on(RoomEvent.Reconnected, () => {
        // Do not silently resume an old capture or replay a completed turn.
        void room.localParticipant.setMicrophoneEnabled(false).catch(() => {});
        const agent = [...room.remoteParticipants.values()].find((p) => p.kind === ParticipantKind.AGENT);
        if (agent) {
          void room.localParticipant.performRpc({ destinationIdentity: agent.identity, method: VOICE_TOPIC, payload: JSON.stringify({ action: "stop_listening" }) }).catch(() => {});
          void room.localParticipant.performRpc({ destinationIdentity: agent.identity, method: VOICE_TOPIC, payload: JSON.stringify({ action: "stop_speaking" }) }).catch(() => {});
        }
      });
      room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
        if (room.canPlaybackAudio || !requestRef.current) return;
        setError("Your browser blocked audio. Select Hear question once to enable it.");
        settleSpeech();
        room.remoteParticipants.forEach((p) => p.setVolume(0));
      });
      room.on(RoomEvent.Disconnected, () => {
        elements.forEach((element) => element.remove());
        if (roomRef.current !== room) return;
        roomRef.current = null;
        captureEpochRef.current += 1;
        setStatus("idle");
        if (!disposedRef.current) setError("Voice disconnected. Select Start listening to reconnect, or type your answer.");
        settleSpeech();
      });
      try {
        await room.connect(credentials.server_url, credentials.participant_token);
        const deadline = Date.now() + 20_000;
        while (![...room.remoteParticipants.values()].some((p) => p.kind === ParticipantKind.AGENT && p.attributes["interview.voice.ready"] === "true")) {
          if (disposedRef.current || connectionEpoch !== connectionEpochRef.current) throw new Error("Voice connection cancelled");
          if (Date.now() > deadline) throw new Error("The voice worker did not connect. Type your answer or try voice again.");
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        if (disposedRef.current || connectionEpoch !== connectionEpochRef.current) throw new Error("Voice connection cancelled");
        return room;
      } catch (failure) {
        if (roomRef.current === room) roomRef.current = null;
        await room.disconnect();
        throw failure;
      }
    })();
    connectingRef.current = task;
    try { return await task; }
    finally { if (connectingRef.current === task) connectingRef.current = null; }
  }, [sessionId, settleSpeech]);

  // Preserve command order even when a connection or microphone permission is pending.
  const rpc = useCallback((command: object, room: Room) => {
    const task = commandQueueRef.current.catch(() => {}).then(async () => {
      if (disposedRef.current || room !== roomRef.current) return;
      const agent = [...room.remoteParticipants.values()].find((p) => p.kind === ParticipantKind.AGENT && p.attributes["interview.voice.ready"] === "true");
      if (!agent) throw new Error("The interview voice worker is unavailable.");
      await room.localParticipant.performRpc({
        destinationIdentity: agent.identity, method: VOICE_TOPIC,
        payload: JSON.stringify(command), responseTimeout: 10_000,
      });
    });
    commandQueueRef.current = task;
    return task;
  }, []);

  const stop = useCallback(() => {
    pushHeldRef.current = false;
    pushActiveRef.current = false;
    captureEpochRef.current += 1;
    setStatus("idle");
    setInputLevel(0);
    const room = roomRef.current;
    if (room?.state === "connected") {
      void room.localParticipant.setMicrophoneEnabled(false).catch(() => {});
      void rpc({ action: "stop_listening" }, room).catch(() => {});
    }
  }, [rpc]);

  const stopSpeech = useCallback(() => {
    settleSpeech();
    const room = roomRef.current;
    if (room?.state === "connected") {
      // Immediately silence local output while the cancellation crosses the network.
      room.remoteParticipants.forEach((p) => p.setVolume(0));
      void rpc({ action: "stop_speaking" }, room).catch(() => {});
    }
  }, [rpc, settleSpeech]);

  const start = useCallback(async () => {
    const epoch = ++captureEpochRef.current;
    setStatus("starting");
    setError("");
    try {
      const room = await connect();
      if (epoch !== captureEpochRef.current) return;
      if (valuesRef.current.mode === "push_to_talk") { setStatus("listening"); return; }
      await rpc({ action: "listen", epoch, turn_index: valuesRef.current.turnIndex }, room);
      if (epoch !== captureEpochRef.current) return;
      await room.switchActiveDevice("audioinput", valuesRef.current.microphones.selectedId || "default");
      if (epoch !== captureEpochRef.current) return;
      await room.localParticipant.setMicrophoneEnabled(true, {
        deviceId: valuesRef.current.microphones.selectedId || undefined,
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      });
      if (epoch !== captureEpochRef.current) {
        await room.localParticipant.setMicrophoneEnabled(false);
        return;
      }
      setStatus("listening");
    } catch (failure) {
      if (epoch !== captureEpochRef.current) return;
      setError((failure as Error).message);
      setStatus("idle");
    }
  }, [connect, rpc]);

  const finishSegment = useCallback(async () => {
    const room = roomRef.current;
    const epoch = captureEpochRef.current;
    if (!room) return;
    setStatus("processing");
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
      await rpc({ action: "flush", epoch }, room);
      if (epoch === captureEpochRef.current) await start();
    } catch (failure) {
      if (epoch === captureEpochRef.current) { setError((failure as Error).message); setStatus("idle"); }
    }
  }, [rpc, start]);

  const beginPush = useCallback(async () => {
    if (pushHeldRef.current) return;
    pushHeldRef.current = true;
    const epoch = ++captureEpochRef.current;
    setStatus("starting");
    try {
      const room = await connect();
      if (epoch !== captureEpochRef.current) return;
      await rpc({ action: "listen", epoch, turn_index: valuesRef.current.turnIndex }, room);
      if (epoch !== captureEpochRef.current) return;
      await room.switchActiveDevice("audioinput", valuesRef.current.microphones.selectedId || "default");
      if (epoch !== captureEpochRef.current) return;
      await room.localParticipant.setMicrophoneEnabled(true, { deviceId: valuesRef.current.microphones.selectedId || undefined });
      if (epoch !== captureEpochRef.current) { await room.localParticipant.setMicrophoneEnabled(false); return; }
      pushActiveRef.current = true;
      setStatus("recording");
    } catch (failure) { if (epoch === captureEpochRef.current) { setError((failure as Error).message); setStatus("idle"); } }
  }, [connect, rpc]);

  const endPush = useCallback(() => {
    if (!pushHeldRef.current) return;
    pushHeldRef.current = false;
    if (!pushActiveRef.current) {
      stop();
      setStatus("listening");
      return;
    }
    pushActiveRef.current = false;
    void finishSegment();
  }, [finishSegment, stop]);

  const play = useCallback(async (turn: number, utterance: string, clarificationIndex?: number) => {
    stop();
    stopSpeech();
    setError("");
    setLoading(true);
    const id = crypto.randomUUID();
    const finished = new Promise<void>((resolve) => {
      requestRef.current = { id, resolve, timer: setTimeout(() => {
        if (requestRef.current?.id !== id) return;
        setError("Interviewer voice timed out. Read the response or replay it.");
        stopSpeech();
      }, 65_000) };
    });
    try {
      const room = await connect();
      if (requestRef.current?.id !== id) return;
      await room.startAudio();
      if (requestRef.current?.id !== id) return;
      if (!room.canPlaybackAudio) throw new Error("Your browser blocked audio. Select Hear question once to enable it.");
      room.remoteParticipants.forEach((p) => p.setVolume(1));
      await rpc({ action: "speak", request_id: id, turn_index: turn, utterance, clarification_index: clarificationIndex }, room);
      await finished;
    } catch (failure) {
      if (requestRef.current?.id !== id) return;
      setError((failure as Error).message || "Select Hear question to enable audio.");
      settleSpeech();
    }
  }, [connect, rpc, settleSpeech, stop, stopSpeech]);

  const speak = useCallback((_session: string, turn: number, _text: string) => play(turn, "question"), [play]);
  const speakReaction = useCallback((_session: string, turn: number, _text: string) => play(turn, "reaction"), [play]);
  const speakClarification = useCallback((_session: string, turn: number, index: number, _text: string) => play(turn, "clarification", index), [play]);
  const setMode = useCallback((next: InterviewVoiceMode) => { stop(); setModeState(next); }, [stop]);

  useEffect(() => {
    if (!enabled) return;
    disposedRef.current = false;
    const meter = setInterval(() => setInputLevel(roomRef.current?.localParticipant.audioLevel ?? 0), 100);
    return () => {
      disposedRef.current = true;
      connectionEpochRef.current += 1;
      captureEpochRef.current += 1;
      connectingRef.current = null;
      clearInterval(meter);
      settleSpeech();
      const room = roomRef.current;
      roomRef.current = null;
      void room?.disconnect();
    };
  }, [enabled, sessionId, settleSpeech]);

  return {
    voice: {
      supported: typeof window !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia),
      mode, setMode, status, inputLevel, microphones, error,
      dismissError: () => setError(""), start, stop, finishSegment,
      beginPush, endPush,
    },
    speech: { speaking, loading, error, speak, speakReaction, speakClarification, stop: stopSpeech },
  };
}
