"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  MAXIMUM_RECORDING_MS,
  microphoneProblem,
  recordingMimeType,
  transcribeRecording,
} from "@/lib/dictation";
import {
  microphoneConstraint,
  refreshMicrophones,
  selectMicrophone,
} from "@/lib/microphone";

export type DictationStatus =
  | "idle"
  | "starting"
  | "recording"
  | "transcribing";

export interface Dictation {
  status: DictationStatus;
  /** Set when something went wrong; typing is always still available. */
  error: string | null;
  elapsedMs: number;
  start(): void;
  /** Finish the recording and transcribe it. */
  stop(): void;
  /** Throw the recording away without spending anything on it. */
  cancel(): void;
  dismissError(): void;
}

const ELAPSED_TICK_MS = 250;

/**
 * Open the chosen microphone, or the default if that one has gone away.
 *
 * The choice is an exact constraint, so a headset unplugged since it was
 * picked fails the request outright rather than recording from the laptop lid
 * without saying so. Falling back is safe here precisely because it is
 * explicit: the stored choice is dropped, so the picker stops claiming a
 * device that no longer exists.
 */
async function openMicrophone(): Promise<MediaStream> {
  const audio = microphoneConstraint();
  try {
    return await navigator.mediaDevices.getUserMedia({ audio });
  } catch (failure) {
    const missing =
      audio !== true &&
      failure instanceof DOMException &&
      (failure.name === "OverconstrainedError" ||
        failure.name === "NotFoundError");
    if (!missing) throw failure;
    selectMicrophone(null);
    return navigator.mediaDevices.getUserMedia({ audio: true });
  }
}

/**
 * Record one spoken question and hand back its text.
 *
 * The recording never becomes a question by itself: the transcript is given
 * to the caller to place in a composer, where it is edited and sent like
 * anything typed. Speech recognition mishears technical terms often enough
 * that auto-sending would spend a retrieval turn on a question the reader
 * never asked.
 */
export function useDictation(onTranscript: (text: string) => void): Dictation {
  const [status, setStatus] = useState<DictationStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const discardedRef = useRef(false);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Held in a ref so a composer re-rendering mid-recording — which it does on
  // every keystroke — cannot detach the recorder's completion handler.
  const deliverRef = useRef(onTranscript);
  const liveRef = useRef(true);

  useEffect(() => {
    deliverRef.current = onTranscript;
  }, [onTranscript]);

  const releaseMicrophone = useCallback(() => {
    if (tickRef.current !== null) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
    // Releases the browser's recording indicator immediately. Leaving the
    // track open leaves that indicator lit long after the question was asked.
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  useEffect(() => {
    liveRef.current = true;
    return () => {
      liveRef.current = false;
      discardedRef.current = true;
      abortRef.current?.abort();
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      releaseMicrophone();
    };
  }, [releaseMicrophone]);

  const send = useCallback(async (recording: Blob) => {
    if (recording.size === 0) {
      if (liveRef.current) {
        setStatus("idle");
        setError("Nothing was recorded.");
      }
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus("transcribing");
    try {
      const text = await transcribeRecording(recording, controller.signal);
      if (!liveRef.current || controller.signal.aborted) return;
      setStatus("idle");
      if (text.trim()) deliverRef.current(text);
      else setError("No speech was recorded.");
    } catch (failure) {
      if (!liveRef.current || controller.signal.aborted) return;
      setStatus("idle");
      setError(
        failure instanceof ApiError
          ? failure.message
          : "That recording could not be transcribed.",
      );
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    recorder.stop();
  }, []);

  const cancel = useCallback(() => {
    discardedRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    else releaseMicrophone();
    setStatus("idle");
    setElapsedMs(0);
  }, [releaseMicrophone]);

  const start = useCallback(async () => {
    if (recorderRef.current) return;
    setError(null);
    setElapsedMs(0);
    setStatus("starting");
    discardedRef.current = false;

    let stream: MediaStream;
    try {
      stream = await openMicrophone();
    } catch (failure) {
      if (!liveRef.current) return;
      setStatus("idle");
      setError(microphoneProblem(failure));
      return;
    }
    // Device labels are unreadable until an origin has been granted the
    // microphone once, so the list is only worth naming after this point.
    void refreshMicrophones();
    // The reader may have cancelled, or navigated, while the permission
    // prompt was open. Nothing should keep recording after that.
    if (!liveRef.current || discardedRef.current) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    const mimeType = recordingMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    } catch {
      stream.getTracks().forEach((track) => track.stop());
      setStatus("idle");
      setError("This browser cannot record audio.");
      return;
    }

    chunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onerror = () => {
      releaseMicrophone();
      if (!liveRef.current) return;
      setStatus("idle");
      setError("Recording stopped unexpectedly.");
    };
    recorder.onstop = () => {
      const parts = chunksRef.current;
      chunksRef.current = [];
      releaseMicrophone();
      setElapsedMs(0);
      if (discardedRef.current || !liveRef.current) return;
      void send(new Blob(parts, { type: recorder.mimeType || mimeType || "" }));
    };

    streamRef.current = stream;
    recorderRef.current = recorder;
    recorder.start();
    setStatus("recording");

    const startedAt = Date.now();
    tickRef.current = setInterval(() => {
      const elapsed = Date.now() - startedAt;
      setElapsedMs(elapsed);
      if (elapsed >= MAXIMUM_RECORDING_MS) stop();
    }, ELAPSED_TICK_MS);
  }, [releaseMicrophone, send, stop]);

  return {
    status,
    error,
    elapsedMs,
    start: useCallback(() => void start(), [start]),
    stop,
    cancel,
    dismissError: useCallback(() => setError(null), []),
  };
}
