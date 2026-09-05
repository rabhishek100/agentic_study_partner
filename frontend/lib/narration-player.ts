/**
 * The one voice in the application, and everything that keeps it speaking.
 *
 * A module-level player rather than per-component state, for a reason that is
 * a bug otherwise: read-aloud controls sit on every turn, in side chats, and
 * on the selection popover, and two of them speaking at once is unusable. One
 * player means starting anywhere stops everywhere else, without every page
 * having to hold a provider or coordinate with its neighbours.
 *
 * Chunks are fetched one ahead of playback. Synthesis of a whole answer takes
 * seconds; synthesis of the first sentence takes a moment, and by the time it
 * has been heard the next chunk has arrived. The listener waits once, briefly.
 */

import { API_BASE, errorDetail } from "./api";
import {
  buildNarrationScript,
  citedFigures,
  narrationChunks,
  type NarrationInput,
} from "./narration";
import { accessToken } from "./supabase";

export type NarrationStatus = "idle" | "preparing" | "speaking" | "paused";

export interface NarrationState {
  status: NarrationStatus;
  /** Which control is speaking, so only that one shows as active. */
  activeId: string | null;
  error: string;
  speed: number;
  /** Position in the script, for the progress a listener can see. */
  chunkIndex: number;
  chunkCount: number;
}

export const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;

const SPEED_STORAGE_KEY = "narration-speed";
const DEFAULT_SPEED = 1;

const IDLE: NarrationState = {
  status: "idle",
  activeId: null,
  error: "",
  speed: DEFAULT_SPEED,
  chunkIndex: 0,
  chunkCount: 0,
};

let state: NarrationState = IDLE;
const listeners = new Set<() => void>();

function emit(next: Partial<NarrationState>): void {
  state = { ...state, ...next };
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function snapshot(): NarrationState {
  return state;
}

/** The server never sees this; it is one reader's preference on one device. */
function storedSpeed(): number {
  try {
    const raw = window.localStorage.getItem(SPEED_STORAGE_KEY);
    const parsed = raw ? Number(raw) : NaN;
    return SPEEDS.includes(parsed as (typeof SPEEDS)[number]) ? parsed : DEFAULT_SPEED;
  } catch {
    // Private windows and blocked site data throw on access rather than
    // returning nothing. A remembered speed is not worth an exception.
    return DEFAULT_SPEED;
  }
}

let hydrated = false;

/** Adopt the remembered speed once, on the first control to mount. */
export function hydrate(): void {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  const speed = storedSpeed();
  if (speed !== state.speed) emit({ speed });
}

// Everything below is the live playback, which is imperative by nature: one
// audio element, one queue, one abort controller.
let audio: HTMLAudioElement | null = null;
let objectUrl: string | null = null;
let generation = 0;
let controller: AbortController | null = null;

function releaseUrl(): void {
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
}

function stopDeviceVoice(): void {
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
}

export function stop(): void {
  generation += 1;
  controller?.abort();
  controller = null;
  if (audio) {
    audio.pause();
    audio.src = "";
  }
  audio = null;
  releaseUrl();
  stopDeviceVoice();
  emit({ status: "idle", activeId: null, chunkIndex: 0, chunkCount: 0 });
}

export function pause(): void {
  if (state.status !== "speaking") return;
  audio?.pause();
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.pause();
  }
  emit({ status: "paused" });
}

export function resume(): void {
  if (state.status !== "paused") return;
  void audio?.play();
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.resume();
  }
  emit({ status: "speaking" });
}

export function setSpeed(speed: number): void {
  emit({ speed });
  // Applied to the element rather than re-synthesised: the browser resamples
  // without shifting pitch, so it is instant and costs nothing.
  if (audio) audio.playbackRate = speed;
  try {
    window.localStorage.setItem(SPEED_STORAGE_KEY, String(speed));
  } catch {
    // Not remembering the speed is a smaller failure than throwing here.
  }
}

async function authorizedHeaders(): Promise<Record<string, string>> {
  const token = await accessToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function fetchDescriptions(
  input: NarrationInput,
  signal: AbortSignal,
): Promise<Record<number, string>> {
  const figures = citedFigures(input);
  if (figures.length === 0) return {};

  const response = await fetch(`${API_BASE}/narration/figures`, {
    method: "POST",
    signal,
    headers: await authorizedHeaders(),
    body: JSON.stringify({
      figures: figures.map((figure) => ({
        book_id: figure.book_id,
        block_id: figure.block_id,
      })),
    }),
  });
  // A figure that cannot be described is not a reason not to read the answer:
  // the script still announces it, it just has nothing to say about it.
  if (!response.ok) return {};
  const payload = (await response.json()) as { descriptions?: Record<string, string> };
  return Object.fromEntries(
    Object.entries(payload.descriptions ?? {}).map(([id, text]) => [Number(id), text]),
  );
}

async function fetchChunk(text: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(`${API_BASE}/narration/speech`, {
    method: "POST",
    signal,
    headers: await authorizedHeaders(),
    body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return response.blob();
}

/** Play one blob to its end, or until the run is superseded. */
function playBlob(blob: Blob, run: number): Promise<void> {
  return new Promise((resolve, reject) => {
    if (run !== generation) {
      resolve();
      return;
    }
    releaseUrl();
    objectUrl = URL.createObjectURL(blob);
    const element = new Audio(objectUrl);
    element.playbackRate = state.speed;
    audio = element;
    element.onended = () => resolve();
    element.onerror = () => reject(new Error("This passage could not be played."));
    element.play().then(
      () => {
        if (run === generation && state.status !== "paused") {
          emit({ status: "speaking" });
        }
      },
      (failure: unknown) => reject(failure),
    );
  });
}

/**
 * The browser's own voice, for when the hosted one cannot be reached.
 *
 * Markedly less natural, and said so in the interface rather than passed off
 * as the same thing. It exists because an answer read in a plain voice beats
 * an answer not read at all when the network is the problem.
 */
function speakOnDevice(text: string, run: number): boolean {
  if (
    typeof window === "undefined" ||
    !("speechSynthesis" in window) ||
    typeof SpeechSynthesisUtterance === "undefined"
  ) return false;

  const utterance = new SpeechSynthesisUtterance(text);
  const voices = window.speechSynthesis.getVoices();
  utterance.voice =
    voices.find(
      (voice) =>
        voice.lang.toLowerCase().startsWith("en") &&
        /(premium|enhanced|natural|google|microsoft|samantha)/i.test(voice.name),
    ) ?? voices.find((voice) => voice.lang.toLowerCase().startsWith("en")) ?? null;
  utterance.rate = state.speed;
  utterance.onend = () => {
    if (run === generation) emit({ status: "idle", activeId: null });
  };
  window.speechSynthesis.speak(utterance);
  emit({ status: "speaking" });
  return true;
}

export async function play(id: string, input: NarrationInput): Promise<void> {
  stop();
  const run = generation;
  controller = new AbortController();
  const { signal } = controller;
  emit({ status: "preparing", activeId: id, error: "", chunkIndex: 0, chunkCount: 0 });

  let chunks: string[] = [];
  try {
    const descriptions = await fetchDescriptions(input, signal);
    if (run !== generation) return;
    chunks = narrationChunks(buildNarrationScript({ ...input, descriptions }));
  } catch (failure) {
    if (signal.aborted || run !== generation) return;
    // Descriptions are the only thing that failed; the prose still reads.
    chunks = narrationChunks(buildNarrationScript(input));
  }

  if (chunks.length === 0) {
    emit({ status: "idle", activeId: null, error: "There is nothing to read here." });
    return;
  }
  emit({ chunkCount: chunks.length });

  // One request ahead: the chunk being heard was fetched while the previous
  // one played.
  let pending: Promise<Blob> | null = fetchChunk(chunks[0]!, signal);

  for (let index = 0; index < chunks.length; index += 1) {
    if (run !== generation) return;
    const current = pending ?? fetchChunk(chunks[index]!, signal);
    const next =
      index + 1 < chunks.length ? fetchChunk(chunks[index + 1]!, signal) : null;
    // Started now so it downloads during playback, and its rejection is
    // handled where it is awaited rather than as an unhandled rejection.
    next?.catch(() => undefined);
    pending = next;

    try {
      const blob = await current;
      if (run !== generation) return;
      emit({ chunkIndex: index });
      await playBlob(blob, run);
    } catch (failure) {
      if (signal.aborted || run !== generation) return;
      const remaining = chunks.slice(index).join(" ");
      if (speakOnDevice(remaining, run)) {
        emit({
          error:
            "The natural voice is unavailable, so this is your browser's own voice.",
        });
        return;
      }
      emit({
        status: "idle",
        activeId: null,
        error:
          failure instanceof Error && failure.message
            ? failure.message
            : "This answer could not be read aloud.",
      });
      return;
    }
  }

  if (run === generation) {
    emit({ status: "idle", activeId: null, chunkIndex: 0, chunkCount: 0 });
  }
}

/** Play, or stop if this control is already the one speaking. */
export async function toggle(id: string, input: NarrationInput): Promise<void> {
  if (state.activeId === id) {
    if (state.status === "speaking") {
      pause();
      return;
    }
    if (state.status === "paused") {
      resume();
      return;
    }
  }
  await play(id, input);
}

/** Test seam: forget the current playback without touching the DOM. */
export function reset(): void {
  generation += 1;
  controller = null;
  audio = null;
  objectUrl = null;
  hydrated = false;
  state = IDLE;
  for (const listener of listeners) listener();
}
