"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ParticipantKind, Room, RoomEvent, Track } from "livekit-client";

import { useMicrophones } from "@/hooks/use-microphones";
import { apiFetch } from "@/lib/api";

const TOPIC = "narration.voice";

type Connection = { server_url: string; participant_token: string };
type VoiceEvent = {
  type: string;
  epoch?: number;
  sequence?: number;
  text?: string;
  request_id?: string;
  message?: string;
};

function parseEvent(payload: Uint8Array): VoiceEvent | null {
  try {
    return JSON.parse(new TextDecoder().decode(payload)) as VoiceEvent;
  } catch {
    return null;
  }
}

export type NarrationVoiceStatus =
  | "off"
  | "connecting"
  | "listening"
  | "hearing"
  | "answering"
  | "ready";

/** LiveKit transports speech only; the page owns the grounded question turn. */
export function useNarrationVoice({
  conversationId,
  onHearing,
  onTranscript,
}: {
  conversationId: string | null;
  onHearing(): void;
  onTranscript(text: string): void;
}) {
  const microphones = useMicrophones();
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState<NarrationVoiceStatus>("off");
  const [error, setError] = useState("");
  const roomRef = useRef<Room | null>(null);
  const connectingRef = useRef<Promise<Room> | null>(null);
  const epochRef = useRef(0);
  const sequenceRef = useRef(0);
  const speechRequestRef = useRef<string | null>(null);
  const valuesRef = useRef({ microphones, onHearing, onTranscript });
  valuesRef.current = { microphones, onHearing, onTranscript };

  const rpc = useCallback(async (room: Room, command: object) => {
    const agent = [...room.remoteParticipants.values()].find(
      (participant) =>
        participant.kind === ParticipantKind.AGENT &&
        participant.attributes["narration.voice.ready"] === "true",
    );
    if (!agent) throw new Error("The read-aloud voice worker is unavailable.");
    await room.localParticipant.performRpc({
      destinationIdentity: agent.identity,
      method: TOPIC,
      payload: JSON.stringify(command),
      responseTimeout: 10_000,
    });
  }, []);

  const disconnect = useCallback(() => {
    epochRef.current += 1;
    speechRequestRef.current = null;
    const room = roomRef.current;
    roomRef.current = null;
    connectingRef.current = null;
    void room?.localParticipant.setMicrophoneEnabled(false).catch(() => {});
    void room?.disconnect();
    setStatus("off");
  }, []);

  const connect = useCallback(async (): Promise<Room> => {
    if (!conversationId) throw new Error("Start a saved conversation before using voice questions.");
    if (roomRef.current?.state === "connected") return roomRef.current;
    if (connectingRef.current) return connectingRef.current;
    const task = (async () => {
      const credentials = await apiFetch<Connection>(
        `/narration/voice-connections/${conversationId}`,
        { method: "POST" },
      );
      const room = new Room({ adaptiveStream: true });
      roomRef.current = room;
      const elements = new Set<HTMLMediaElement>();
      room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
        if (track.kind !== Track.Kind.Audio || participant.kind !== ParticipantKind.AGENT) return;
        const element = track.attach();
        elements.add(element);
        element.hidden = true;
        document.body.appendChild(element);
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((element) => {
          elements.delete(element);
          element.remove();
        });
      });
      room.on(RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
        if (topic !== TOPIC || participant?.kind !== ParticipantKind.AGENT) return;
        const event = parseEvent(payload);
        if (!event) return;
        if (event.epoch === epochRef.current && event.type === "hearing") {
          valuesRef.current.onHearing();
          setStatus("hearing");
        } else if (
          event.epoch === epochRef.current &&
          event.type === "transcript" &&
          event.text &&
          (event.sequence ?? 0) > sequenceRef.current
        ) {
          sequenceRef.current = event.sequence ?? sequenceRef.current;
          valuesRef.current.onTranscript(event.text);
          setStatus("hearing");
        } else if (event.type === "capture_error") {
          setError(event.message ?? "Voice transcription stopped.");
          setStatus("ready");
        } else if (event.request_id === speechRequestRef.current) {
          if (event.type === "speech_started") setStatus("answering");
          if (event.type === "speech_done") {
            speechRequestRef.current = null;
            setStatus("ready");
          }
          if (event.type === "speech_error") {
            speechRequestRef.current = null;
            setError(event.message ?? "The voice answer is unavailable.");
            setStatus("ready");
          }
        }
      });
      room.on(RoomEvent.Reconnecting, () => setStatus("connecting"));
      room.on(RoomEvent.Disconnected, () => {
        elements.forEach((element) => element.remove());
        if (roomRef.current === room) {
          roomRef.current = null;
          setStatus(enabled ? "ready" : "off");
          setError("Voice disconnected. Select the microphone to reconnect.");
        }
      });
      try {
        await room.connect(credentials.server_url, credentials.participant_token);
        const deadline = Date.now() + 20_000;
        while (![...room.remoteParticipants.values()].some(
          (participant) => participant.attributes["narration.voice.ready"] === "true",
        )) {
          if (Date.now() > deadline) throw new Error("The read-aloud voice worker did not connect.");
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        return room;
      } catch (failure) {
        if (roomRef.current === room) roomRef.current = null;
        await room.disconnect();
        throw failure;
      }
    })();
    connectingRef.current = task;
    try {
      return await task;
    } finally {
      if (connectingRef.current === task) connectingRef.current = null;
    }
  }, [conversationId, enabled]);

  const startListening = useCallback(async () => {
    setStatus("connecting");
    setError("");
    const epoch = ++epochRef.current;
    sequenceRef.current = 0;
    try {
      const room = await connect();
      if (epoch !== epochRef.current) return;
      await rpc(room, { action: "listen", epoch });
      await room.switchActiveDevice(
        "audioinput",
        valuesRef.current.microphones.selectedId || "default",
      );
      await room.localParticipant.setMicrophoneEnabled(true, {
        deviceId: valuesRef.current.microphones.selectedId || undefined,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      if (epoch !== epochRef.current) {
        await room.localParticipant.setMicrophoneEnabled(false);
        return;
      }
      setStatus("listening");
    } catch (failure) {
      setError((failure as Error).message);
      setStatus("ready");
    }
  }, [connect, rpc]);

  const stopListening = useCallback(async () => {
    const epoch = epochRef.current;
    const room = roomRef.current;
    if (!room) return;
    await room.localParticipant.setMicrophoneEnabled(false).catch(() => {});
    await rpc(room, { action: "flush", epoch }).catch(() => {});
    setStatus("ready");
  }, [rpc]);

  const speakAnswer = useCallback(async (sideChatId: string, turnIndex: number) => {
    try {
      const room = await connect();
      const requestId = crypto.randomUUID();
      speechRequestRef.current = requestId;
      setStatus("answering");
      await rpc(room, {
        action: "speak",
        request_id: requestId,
        side_chat_id: sideChatId,
        turn_index: turnIndex,
      });
    } catch (failure) {
      setError((failure as Error).message);
      setStatus("ready");
    }
  }, [connect, rpc]);

  const updateEnabled = useCallback((next: boolean) => {
    setEnabled(next);
    setError("");
    if (next) {
      setStatus("ready");
      void startListening();
    } else {
      disconnect();
    }
  }, [disconnect, startListening]);

  useEffect(() => disconnect, [conversationId, disconnect]);

  return {
    enabled,
    setEnabled: updateEnabled,
    status,
    error,
    dismissError: () => setError(""),
    startListening,
    stopListening,
    speakAnswer,
    microphones,
    supported: typeof window !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia),
  };
}
