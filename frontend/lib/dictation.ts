"use client";

import { ApiError, errorDetail, uploadUrl } from "./api";
import { accessToken } from "./supabase";

/**
 * Containers to try, best first.
 *
 * Chrome and Firefox record Opus in WebM; Safari records AAC in MP4 and
 * supports none of the WebM entries. The list is a preference order rather
 * than a capability claim — every entry is checked against the browser before
 * it is used, and an unrecognised recorder falls back to whatever default it
 * chooses.
 */
const CANDIDATE_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

/**
 * How long one dictated question may run.
 *
 * Speaking a question takes seconds. The cap is here so a mic left open by a
 * mistake — a tab switched away from mid-recording — cannot quietly buy
 * minutes of transcription.
 */
export const MAXIMUM_RECORDING_MS = 120_000;

/** Whether this browser, on this origin, can record at all. */
export function canDictate(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined" &&
    // Undefined outside a secure context, which is how a dev server reached
    // over a LAN address behaves even though the browser itself supports it.
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

/** The first container this browser will record, or null to accept its default. */
export function recordingMimeType(): string | null {
  if (typeof window === "undefined" || !window.MediaRecorder) return null;
  const isSupported = window.MediaRecorder.isTypeSupported;
  if (typeof isSupported !== "function") return null;
  return CANDIDATE_MIME_TYPES.find((type) => isSupported(type)) ?? null;
}

export async function transcribeRecording(
  recording: Blob,
  signal?: AbortSignal,
): Promise<string> {
  const token = await accessToken();
  const response = await fetch(uploadUrl("/transcriptions"), {
    method: "POST",
    headers: {
      // A recorder that chose its own container still names it on the blob.
      "Content-Type": recording.type || "audio/webm",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: recording,
    signal,
  });
  if (!response.ok) {
    throw new ApiError(await errorDetail(response), response.status);
  }
  const body = (await response.json()) as { text?: string };
  return body.text ?? "";
}

export interface SplicedTranscript {
  value: string;
  caret: number;
}

/**
 * Put dictated words where the caret is, spaced like typing would be.
 *
 * Dictation is an alternative to the keyboard, not a separate channel: a
 * reader who has typed half a question and then speaks the rest expects the
 * words to land at the caret and to be readable without repairing the spacing
 * by hand. Any selected text is replaced, exactly as typing over it would.
 */
export function spliceTranscript(
  value: string,
  transcript: string,
  selectionStart: number,
  selectionEnd: number = selectionStart,
): SplicedTranscript {
  const spoken = transcript.trim();
  if (!spoken) return { value, caret: selectionEnd };
  const start = Math.max(0, Math.min(selectionStart, value.length));
  const end = Math.max(start, Math.min(selectionEnd, value.length));
  const before = value.slice(0, start);
  const after = value.slice(end);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const trail = after && !/^\s/.test(after) ? " " : "";
  return {
    value: `${before}${lead}${spoken}${trail}${after}`,
    caret: before.length + lead.length + spoken.length,
  };
}

/** mm:ss, so a recording's length is readable while it runs. */
export function recordingClock(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1_000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * What to tell the reader when the microphone itself refuses.
 *
 * Every branch names the fix. A blocked microphone is a settings problem the
 * reader can solve, and none of these states stop them typing the question.
 */
export function microphoneProblem(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone access is blocked. Allow it in your browser settings.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "No microphone was found.";
  }
  if (name === "NotReadableError") {
    return "The microphone is in use by another app.";
  }
  return "The microphone could not be started.";
}
