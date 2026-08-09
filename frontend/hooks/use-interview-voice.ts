"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useMicrophones } from "@/hooks/use-microphones";
import { microphoneProblem, recordingMimeType } from "@/lib/dictation";
import { openSpeechMicrophone } from "@/lib/microphone";

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
const CALIBRATION_MS = 450;
const MINIMUM_RMS_THRESHOLD = 0.0045;
const MAXIMUM_RMS_THRESHOLD = 0.025;
const NOISE_MULTIPLIER = 1.8;
const LOUD_FRAMES_TO_START = 3;
// A click, chair movement, or speaker echo can cross the level threshold for
// one or two animation frames. Requiring sustained voiced frames prevents
// those clips from reaching Whisper, where silence can become stock text such
// as "Thank you."
const MINIMUM_VOICED_FRAMES = 8;

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

export function useInterviewVoice({
  onRecording,
  onVoiceStart,
}: {
  onRecording: (recording: Blob) => Promise<void>;
  onVoiceStart?: () => void;
}) {
  const microphones = useMicrophones();
  const [mode, setMode] = useState<InterviewVoiceMode>("automatic");
  const [status, setStatus] = useState<InterviewVoiceStatus>("idle");
  const [inputLevel, setInputLevel] = useState(0);
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
  const captureGenerationRef = useRef(0);
  const recordingGenerationRef = useRef(0);
  const modeRef = useRef(mode);
  const onRecordingRef = useRef(onRecording);
  const onVoiceStartRef = useRef(onVoiceStart);
  const loudFramesRef = useRef(0);
  const voicedFramesRef = useRef(0);
  const startedAtRef = useRef(0);
  const lastLoudAtRef = useRef(0);
  const calibrationUntilRef = useRef(0);
  const noiseFloorRef = useRef(0.0025);
  const lastMeterAtRef = useRef(0);
  const samplesRef = useRef<Float32Array<ArrayBuffer> | null>(null);

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
    captureGenerationRef.current += 1;
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
    samplesRef.current = null;
    loudFramesRef.current = 0;
    voicedFramesRef.current = 0;
    noiseFloorRef.current = 0.0025;
    setInputLevel(0);
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
      const captureGeneration = recordingGenerationRef.current;
      const duration = Date.now() - startedAtRef.current;
      const voicedFrames = voicedFramesRef.current;
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || mimeType || "audio/webm",
      });
      chunksRef.current = [];
      recorderRef.current = null;
      loudFramesRef.current = 0;
      voicedFramesRef.current = 0;
      if (
        !listeningRef.current ||
        duration < MINIMUM_SPEECH_MS ||
        voicedFrames < MINIMUM_VOICED_FRAMES ||
        !blob.size
      ) {
        if (listeningRef.current) setStatus("listening");
        return;
      }
      pendingTranscriptionsRef.current += 1;
      setStatus("processing");
      transcriptionQueueRef.current = transcriptionQueueRef.current
        .then(() => {
          if (captureGeneration !== captureGenerationRef.current) return;
          return onRecordingRef.current(blob);
        })
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
    voicedFramesRef.current = loudFramesRef.current;
    recordingGenerationRef.current = captureGenerationRef.current;
    recorderRef.current = recorder;
    recorder.start(250);
    setStatus("recording");
    onVoiceStartRef.current?.();
  }, []);

  const monitor = useCallback(() => {
    const analyser = analyserRef.current;
    if (!listeningRef.current || !analyser) return;
    const samples = samplesRef.current ?? new Float32Array(analyser.fftSize);
    samplesRef.current = samples;
    analyser.getFloatTimeDomainData(samples);
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    const rms = Math.sqrt(energy / samples.length);
    const now = Date.now();
    const recorder = recorderRef.current;
    const threshold = Math.max(
      MINIMUM_RMS_THRESHOLD,
      Math.min(MAXIMUM_RMS_THRESHOLD, noiseFloorRef.current * NOISE_MULTIPLIER),
    );
    const loud = rms >= threshold && rms - noiseFloorRef.current >= 0.001;

    if (now - lastMeterAtRef.current >= 80) {
      const db = 20 * Math.log10(Math.max(rms, 0.001));
      setInputLevel(Math.max(0, Math.min(1, (db + 60) / 45)));
      lastMeterAtRef.current = now;
    }

    if (modeRef.current === "automatic") {
      if (!recorder) {
        if (now < calibrationUntilRef.current) {
          // Learn this device's room tone before deciding what speech looks
          // like. This replaces the old one-size threshold that ignored quiet
          // laptop and headset microphones entirely.
          noiseFloorRef.current = noiseFloorRef.current * 0.85 + rms * 0.15;
          loudFramesRef.current = 0;
        } else {
          loudFramesRef.current = loud ? loudFramesRef.current + 1 : 0;
          if (!loud && rms < threshold) {
            noiseFloorRef.current = noiseFloorRef.current * 0.985 + rms * 0.015;
          }
          if (loudFramesRef.current >= LOUD_FRAMES_TO_START) beginRecording();
        }
      } else if (recorder.state === "recording") {
        // A slightly softer continuation threshold avoids chopping off the
        // end of a sentence after speech has already been established.
        if (rms >= threshold * 0.72) {
          lastLoudAtRef.current = now;
          voicedFramesRef.current += 1;
        }
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
      const stream = await openSpeechMicrophone();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.25;
      source.connect(analyser);
      streamRef.current = stream;
      contextRef.current = context;
      analyserRef.current = analyser;
      samplesRef.current = new Float32Array(analyser.fftSize);
      noiseFloorRef.current = 0.0025;
      calibrationUntilRef.current = Date.now() + CALIBRATION_MS;
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
    inputLevel,
    microphones,
    error,
    dismissError: () => setError(""),
    start,
    stop: release,
    beginPush,
    endPush,
  };
}
