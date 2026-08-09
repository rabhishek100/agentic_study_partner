"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { microphoneProblem, recordingMimeType } from "@/lib/dictation";
import { microphoneConstraint, selectMicrophone } from "@/lib/microphone";

export type InterviewVoiceMode = "automatic" | "push_to_talk";
export type InterviewVoiceStatus =
  | "idle"
  | "starting"
  | "listening"
  | "recording"
  | "processing";

const SILENCE_MS = 1_200;
const MINIMUM_SPEECH_MS = 280;
const MAXIMUM_SPEECH_MS = 180_000;
const RMS_THRESHOLD = 0.028;
const LOUD_FRAMES_TO_START = 3;

let primedAudioContext: AudioContext | null = null;

/** Unlock Web Audio during the setup/resume click before route/API awaits. */
export function primeInterviewAudio(): void {
  if (typeof AudioContext === "undefined") return;
  if (primedAudioContext && primedAudioContext.state !== "closed") return;
  const context = new AudioContext();
  primedAudioContext = context;
  void context.resume();
}

export function releasePrimedInterviewAudio(): void {
  const context = primedAudioContext;
  primedAudioContext = null;
  if (context && context.state !== "closed") void context.close();
}

function audioContext(): AudioContext {
  const context = primedAudioContext;
  primedAudioContext = null;
  if (context && context.state !== "closed") return context;
  return new AudioContext();
}

async function microphone(): Promise<MediaStream> {
  const audio = microphoneConstraint();
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio:
        audio === true
          ? { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
          : {
              ...audio,
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            },
    });
  } catch (failure) {
    const missing =
      audio !== true &&
      failure instanceof DOMException &&
      (failure.name === "OverconstrainedError" || failure.name === "NotFoundError");
    if (!missing) throw failure;
    selectMicrophone(null);
    return navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  }
}

export function useInterviewVoice({
  onRecording,
  onVoiceStart,
}: {
  onRecording: (recording: Blob) => Promise<void>;
  onVoiceStart?: () => void;
}) {
  const [mode, setMode] = useState<InterviewVoiceMode>("automatic");
  const [status, setStatus] = useState<InterviewVoiceStatus>("idle");
  const [error, setError] = useState("");
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const frameRef = useRef<number | null>(null);
  const listeningRef = useRef(false);
  const transcriptionQueueRef = useRef<Promise<void>>(Promise.resolve());
  const pendingTranscriptionsRef = useRef(0);
  const modeRef = useRef(mode);
  const onRecordingRef = useRef(onRecording);
  const onVoiceStartRef = useRef(onVoiceStart);
  const loudFramesRef = useRef(0);
  const startedAtRef = useRef(0);
  const lastLoudAtRef = useRef(0);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);
  useEffect(() => {
    onRecordingRef.current = onRecording;
  }, [onRecording]);
  useEffect(() => {
    onVoiceStartRef.current = onVoiceStart;
  }, [onVoiceStart]);

  const release = useCallback(() => {
    listeningRef.current = false;
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void contextRef.current?.close();
    contextRef.current = null;
    analyserRef.current = null;
    setStatus("idle");
  }, []);

  useEffect(() => release, [release]);

  const finishRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    recorder.stop();
  }, []);

  const beginRecording = useCallback(() => {
    const stream = streamRef.current;
    if (!stream || recorderRef.current) return;
    const mimeType = recordingMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      const duration = Date.now() - startedAtRef.current;
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || mimeType || "audio/webm",
      });
      chunksRef.current = [];
      recorderRef.current = null;
      loudFramesRef.current = 0;
      if (!listeningRef.current || duration < MINIMUM_SPEECH_MS || !blob.size) {
        if (listeningRef.current) setStatus("listening");
        return;
      }
      pendingTranscriptionsRef.current += 1;
      setStatus("processing");
      transcriptionQueueRef.current = transcriptionQueueRef.current
        .then(() => onRecordingRef.current(blob))
        .catch((failure) => {
          setError((failure as Error).message || "That answer could not be transcribed.");
        })
        .finally(() => {
          pendingTranscriptionsRef.current = Math.max(
            0,
            pendingTranscriptionsRef.current - 1,
          );
          if (listeningRef.current && !recorderRef.current) {
            setStatus(
              pendingTranscriptionsRef.current > 0 ? "processing" : "listening",
            );
          }
        });
    };
    recorder.onerror = () => {
      recorderRef.current = null;
      setError("Recording stopped unexpectedly.");
      if (listeningRef.current) setStatus("listening");
    };
    startedAtRef.current = Date.now();
    lastLoudAtRef.current = startedAtRef.current;
    recorderRef.current = recorder;
    recorder.start(250);
    setStatus("recording");
    onVoiceStartRef.current?.();
  }, []);

  const monitor = useCallback(() => {
    const analyser = analyserRef.current;
    if (!listeningRef.current || !analyser) return;
    const samples = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(samples);
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    const loud = Math.sqrt(energy / samples.length) >= RMS_THRESHOLD;
    const now = Date.now();
    const recorder = recorderRef.current;

    if (modeRef.current === "automatic") {
      if (!recorder) {
        loudFramesRef.current = loud ? loudFramesRef.current + 1 : 0;
        if (loudFramesRef.current >= LOUD_FRAMES_TO_START) beginRecording();
      } else if (recorder.state === "recording") {
        if (loud) lastLoudAtRef.current = now;
        const silentLongEnough = now - lastLoudAtRef.current >= SILENCE_MS;
        const tooLong = now - startedAtRef.current >= MAXIMUM_SPEECH_MS;
        if (silentLongEnough || tooLong) finishRecording();
      }
    }
    frameRef.current = requestAnimationFrame(monitor);
  }, [beginRecording, finishRecording]);

  const start = useCallback(async () => {
    if (streamRef.current) return;
    setStatus("starting");
    setError("");
    const context = audioContext();
    try {
      await context.resume();
    } catch {
      // The explicit state check below produces one useful instruction for
      // every browser-specific autoplay error.
    }
    if (context.state !== "running") {
      void context.close();
      setStatus("idle");
      setError("Your browser paused microphone analysis. Select Start listening once to enable it.");
      return;
    }
    try {
      const stream = await microphone();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.25;
      source.connect(analyser);
      streamRef.current = stream;
      contextRef.current = context;
      analyserRef.current = analyser;
      listeningRef.current = true;
      setStatus("listening");
      frameRef.current = requestAnimationFrame(monitor);
    } catch (failure) {
      void context.close();
      setStatus("idle");
      setError(microphoneProblem(failure));
    }
  }, [monitor]);

  const beginPush = useCallback(() => {
    if (modeRef.current === "push_to_talk") beginRecording();
  }, [beginRecording]);

  const endPush = useCallback(() => {
    if (modeRef.current === "push_to_talk") finishRecording();
  }, [finishRecording]);

  return {
    supported:
      typeof window !== "undefined" &&
      typeof AudioContext !== "undefined" &&
      typeof MediaRecorder !== "undefined" &&
      Boolean(navigator.mediaDevices?.getUserMedia),
    mode,
    setMode,
    status,
    error,
    dismissError: () => setError(""),
    start,
    stop: release,
    beginPush,
    endPush,
  };
}
