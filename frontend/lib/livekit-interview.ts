export const LIVEKIT_INTERVIEWS = process.env.NEXT_PUBLIC_INTERVIEW_LIVEKIT_ENABLED === "true";
export const VOICE_TOPIC = "interview.voice";

export type VoiceEvent = {
  type: "transcript" | "hearing" | "capture_error" | "speech_started" | "speech_done" | "speech_error";
  epoch?: number;
  sequence?: number;
  text?: string;
  request_id?: string;
  message?: string;
};

export function parseVoiceEvent(payload: Uint8Array): VoiceEvent | null {
  try {
    const value = JSON.parse(new TextDecoder().decode(payload));
    if (!value || typeof value !== "object") return null;
    if (["transcript", "hearing", "capture_error"].includes(value.type)) {
      if (!Number.isSafeInteger(value.epoch)) return null;
      if (value.type === "transcript" && (
        !Number.isSafeInteger(value.sequence) || typeof value.text !== "string"
      )) return null;
    } else if (["speech_started", "speech_done", "speech_error"].includes(value.type)) {
      if (typeof value.request_id !== "string") return null;
    } else return null;
    return value as VoiceEvent;
  } catch { return null; }
}

/** One ordered stream per room; only the active capture may update the draft. */
export function acceptsTranscript(event: VoiceEvent, epoch: number, lastSequence: number): boolean {
  return event.type === "transcript" && event.epoch === epoch &&
    typeof event.sequence === "number" && event.sequence > lastSequence &&
    Boolean(event.text?.trim());
}
