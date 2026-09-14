"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ParticipantKind, Room, RoomEvent, Track } from "livekit-client";

import { apiFetch } from "@/lib/api";
import {
  buildIdealTimeline,
  segmentAt,
  type IdealSpeaker,
  type IdealTimelineSegment,
} from "@/lib/ideal-interview-timeline";
import type { IdealInterviewExchange } from "@/lib/interview-types";

type Connection = { server_url: string; participant_token: string };
export type IdealVoiceChoice = "voice_one" | "voice_two";
export type IdealDelivery = "balanced" | "calm" | "animated";
type PlaybackStatus = "idle" | "connecting" | "playing" | "paused" | "complete";

export interface IdealPlaybackSettings {
  speed: number;
  interviewerVoice: IdealVoiceChoice;
  candidateVoice: IdealVoiceChoice;
  delivery: IdealDelivery;
}

const DEFAULT_SETTINGS: IdealPlaybackSettings = {
  speed: 0.96,
  interviewerVoice: "voice_one",
  candidateVoice: "voice_two",
  delivery: "balanced",
};
const SETTINGS_KEY = "ideal-interview-playback-settings-v1";

export function useIdealInterviewPlayback(
  flowId: string,
  exchanges: IdealInterviewExchange[],
) {
  const timeline = useMemo(() => buildIdealTimeline(exchanges), [exchanges]);
  const duration = timeline.at(-1)?.end ?? 0;
  const [status, setStatus] = useState<PlaybackStatus>("idle");
  const [error, setError] = useState("");
  const [exchangeIndex, setExchangeIndex] = useState(0);
  const [speaker, setSpeaker] = useState<IdealSpeaker>("interviewer");
  const [sentenceIndex, setSentenceIndex] = useState(0);
  const [position, setPosition] = useState(0);
  const [settings, setSettings] = useState<IdealPlaybackSettings>(DEFAULT_SETTINGS);
  const roomRef = useRef<Room | null>(null);
  const disposedRef = useRef(false);
  const anchorRef = useRef({ position: 0, at: 0, end: 0 });

  const anchor = useCallback((next: number, end = duration) => {
    const bounded = Math.max(0, Math.min(next, duration));
    anchorRef.current = { position: bounded, at: performance.now(), end };
    setPosition(bounded);
  }, [duration]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null") as Partial<IdealPlaybackSettings> | null;
      if (saved) setSettings({ ...DEFAULT_SETTINGS, ...saved });
    } catch { /* Invalid local preferences fall back to reviewed defaults. */ }
  }, []);

  useEffect(() => {
    if (status !== "playing") return;
    const timer = window.setInterval(() => {
      const current = anchorRef.current;
      const advanced = current.position + (performance.now() - current.at) / 1_000 * settings.speed;
      setPosition(Math.min(current.end, advanced));
    }, 200);
    return () => window.clearInterval(timer);
  }, [settings.speed, status]);

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
          type: string;
          speaker?: IdealSpeaker;
          exchange_index?: number;
          sentence_index?: number;
          message?: string;
        };
        if (event.type === "speaker_started" && event.speaker && event.exchange_index !== undefined) {
          const sentence = event.sentence_index ?? 0;
          const active = timeline.find((item) =>
            item.exchangeIndex === event.exchange_index
            && item.speaker === event.speaker
            && item.sentenceIndex === sentence,
          );
          const utteranceEnd = [...timeline].reverse().find((item) =>
            item.exchangeIndex === event.exchange_index && item.speaker === event.speaker,
          )?.end ?? active?.end ?? duration;
          setSpeaker(event.speaker);
          setExchangeIndex(event.exchange_index);
          setSentenceIndex(sentence);
          anchor(active?.start ?? 0, utteranceEnd);
          setStatus("playing");
        } else if (event.type === "speaker_done" && event.speaker && event.exchange_index !== undefined) {
          const utteranceEnd = [...timeline].reverse().find((item) =>
            item.exchangeIndex === event.exchange_index && item.speaker === event.speaker,
          )?.end;
          if (utteranceEnd !== undefined) anchor(utteranceEnd, utteranceEnd);
          if (event.speaker === "interviewer") {
            setSpeaker("candidate");
            setSentenceIndex(0);
          } else {
            setExchangeIndex(event.exchange_index + 1);
            setSpeaker("interviewer");
            setSentenceIndex(0);
          }
        } else if (event.type === "playback_done") {
          anchor(duration, duration);
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
  }, [anchor, duration, flowId, timeline]);

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

  const startAt = useCallback(async (
    target: IdealTimelineSegment,
    nextSettings: IdealPlaybackSettings = settings,
  ) => {
    setStatus("connecting");
    setError("");
    const room = await connect();
    await room.startAudio();
    await command({
      action: "play",
      start_exchange: target.exchangeIndex,
      start_speaker: target.speaker,
      start_sentence: target.sentenceIndex,
      speed: nextSettings.speed,
      interviewer_voice: nextSettings.interviewerVoice,
      candidate_voice: nextSettings.candidateVoice,
      delivery: nextSettings.delivery,
      request_id: crypto.randomUUID(),
    });
    setExchangeIndex(target.exchangeIndex);
    setSpeaker(target.speaker);
    setSentenceIndex(target.sentenceIndex);
    const utteranceEnd = [...timeline].reverse().find((item) =>
      item.exchangeIndex === target.exchangeIndex && item.speaker === target.speaker,
    )?.end ?? target.end;
    anchor(target.start, utteranceEnd);
    setStatus("playing");
  }, [anchor, command, connect, settings, timeline]);

  const play = useCallback(async () => {
    const target = status === "complete" ? timeline[0] : segmentAt(timeline, position);
    if (!target) return;
    try {
      await startAt(target);
    } catch (failure) {
      setStatus("paused");
      setError((failure as Error).message);
    }
  }, [position, startAt, status, timeline]);

  const pause = useCallback(async () => {
    try { await command({ action: "stop" }); } catch { /* Already disconnected is paused. */ }
    setStatus("paused");
  }, [command]);

  const seekTo = useCallback(async (seconds: number) => {
    const target = segmentAt(timeline, seconds);
    if (!target) return;
    const resume = status === "playing" || status === "connecting";
    try { await command({ action: "stop" }); } catch { /* A seek before first play needs no stop. */ }
    setExchangeIndex(target.exchangeIndex);
    setSpeaker(target.speaker);
    setSentenceIndex(target.sentenceIndex);
    anchor(target.start, target.end);
    setStatus("paused");
    if (resume) {
      try { await startAt(target); } catch (failure) {
        setError((failure as Error).message);
        setStatus("paused");
      }
    }
  }, [anchor, command, startAt, status, timeline]);

  const seekBy = useCallback((seconds: number) => {
    void seekTo(position + seconds);
  }, [position, seekTo]);

  const seekExchange = useCallback((nextExchange: number) => {
    const target = timeline.find((item) => item.exchangeIndex === nextExchange);
    if (target) void seekTo(target.start);
  }, [seekTo, timeline]);

  const applySettings = useCallback((next: IdealPlaybackSettings) => {
    setSettings(next);
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
    if (status !== "playing" && status !== "connecting") return;
    const target = segmentAt(timeline, position);
    if (!target) return;
    void command({ action: "stop" })
      .then(() => startAt(target, next))
      .catch((failure: Error) => {
        setError(failure.message);
        setStatus("paused");
      });
  }, [command, position, startAt, status, timeline]);

  useEffect(() => () => {
    disposedRef.current = true;
    void roomRef.current?.disconnect();
  }, []);

  return {
    status,
    error,
    exchangeIndex,
    speaker,
    position,
    duration,
    settings,
    play,
    pause,
    seekTo,
    seekBy,
    seekExchange,
    applySettings,
  };
}
