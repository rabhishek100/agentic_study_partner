"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ParticipantKind, Room, RoomEvent, Track } from "livekit-client";

import { apiFetch } from "@/lib/api";

type Connection = { server_url: string; participant_token: string };
type Speaker = "interviewer" | "candidate";

export function useIdealInterviewPlayback(flowId: string) {
  const [status, setStatus] = useState<"idle" | "connecting" | "playing" | "paused" | "complete">("idle");
  const [error, setError] = useState("");
  const [exchangeIndex, setExchangeIndex] = useState(0);
  const [speaker, setSpeaker] = useState<Speaker>("interviewer");
  const roomRef = useRef<Room | null>(null);
  const disposedRef = useRef(false);

  const connect = useCallback(async () => {
    if (roomRef.current?.state === "connected") return roomRef.current;
    const credentials = await apiFetch<Connection>(`/ideal-interviews/${flowId}/voice-connection`, { method: "POST" });
    if (disposedRef.current) throw new Error("Playback connection cancelled");
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
      track.detach().forEach((element) => { elements.delete(element); element.remove(); });
    });
    room.on(RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
      if (topic !== "ideal-interview.voice" || participant?.kind !== ParticipantKind.AGENT) return;
      try {
        const event = JSON.parse(new TextDecoder().decode(payload)) as {
          type: string; speaker?: Speaker; exchange_index?: number; message?: string;
        };
        if (event.type === "speaker_started" && event.speaker && event.exchange_index !== undefined) {
          setSpeaker(event.speaker);
          setExchangeIndex(event.exchange_index);
          setStatus("playing");
        } else if (event.type === "speaker_done" && event.speaker && event.exchange_index !== undefined) {
          if (event.speaker === "interviewer") {
            setSpeaker("candidate");
          } else {
            setExchangeIndex(event.exchange_index + 1);
            setSpeaker("interviewer");
          }
        } else if (event.type === "playback_done") {
          setStatus("complete");
        } else if (event.type === "playback_error") {
          setStatus("paused");
          setError(event.message || "Playback stopped.");
        }
      } catch { /* Ignore untrusted room data that is not our event contract. */ }
    });
    room.on(RoomEvent.Disconnected, () => {
      elements.forEach((element) => element.remove());
      if (roomRef.current === room) roomRef.current = null;
      if (!disposedRef.current) setStatus("paused");
    });
    await room.connect(credentials.server_url, credentials.participant_token);
    const deadline = Date.now() + 20_000;
    while (![...room.remoteParticipants.values()].some(
      (item) => item.kind === ParticipantKind.AGENT && item.attributes["ideal.interview.voice.ready"] === "true",
    )) {
      if (Date.now() > deadline) throw new Error("The ideal-interview voice worker did not connect.");
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    return room;
  }, [flowId]);

  const command = useCallback(async (payload: object) => {
    const room = roomRef.current;
    if (!room) throw new Error("Playback is not connected.");
    const agent = [...room.remoteParticipants.values()].find(
      (item) => item.kind === ParticipantKind.AGENT && item.attributes["ideal.interview.voice.ready"] === "true",
    );
    if (!agent) throw new Error("The ideal-interview voice worker is unavailable.");
    await room.localParticipant.performRpc({
      destinationIdentity: agent.identity,
      method: "ideal-interview.voice",
      payload: JSON.stringify(payload),
      responseTimeout: 10_000,
    });
  }, []);

  const play = useCallback(async () => {
    setStatus("connecting");
    setError("");
    try {
      const room = await connect();
      await room.startAudio();
      const replaying = status === "complete";
      if (replaying) {
        setExchangeIndex(0);
        setSpeaker("interviewer");
      }
      await command({
        action: "play",
        start_exchange: replaying ? 0 : exchangeIndex,
        start_speaker: replaying ? "interviewer" : speaker,
        request_id: crypto.randomUUID(),
      });
      setStatus("playing");
    } catch (failure) {
      setStatus("paused");
      setError((failure as Error).message);
    }
  }, [command, connect, exchangeIndex, speaker, status]);

  const pause = useCallback(async () => {
    try { await command({ action: "stop" }); } catch { /* Already disconnected is paused. */ }
    setStatus("paused");
  }, [command]);

  const seek = useCallback((next: number) => {
    void pause();
    setExchangeIndex(next);
    setSpeaker("interviewer");
  }, [pause]);

  useEffect(() => () => {
    disposedRef.current = true;
    void roomRef.current?.disconnect();
  }, []);

  return { status, error, exchangeIndex, speaker, play, pause, seek };
}
