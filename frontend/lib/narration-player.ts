/** The application's single, sentence-addressable narration player. */

import { API_BASE, errorDetail } from "./api";
import {
  buildNarrationScript,
  citedFigures,
  narrationItems,
  type NarrationInput,
  type NarrationItem,
} from "./narration";
import { accessToken } from "./supabase";

export type NarrationStatus = "idle" | "preparing" | "speaking" | "paused";

export interface NarrationState {
  status: NarrationStatus;
  activeId: string | null;
  /** DOM passage to follow; may differ from activeId for “Read exchange”. */
  anchorId: string | null;
  label: string;
  error: string;
  speed: number;
  chunkIndex: number;
  chunkCount: number;
  currentText: string;
  currentAnchor: NarrationItem["anchor"] | null;
  currentTime: number;
  duration: number;
  durationEstimated: boolean;
  autoFollow: boolean;
  voiceContext: NarrationVoiceContext | null;
}

export interface NarrationVoiceContext {
  conversationId: string;
  turnIndex: number;
}

export interface PlaybackOptions {
  anchorId?: string;
  label?: string;
  voiceContext?: NarrationVoiceContext;
}

export const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;
const SPEED_STORAGE_KEY = "narration-speed";
const FOLLOW_STORAGE_KEY = "narration-auto-follow";
const DEFAULT_SPEED = 1;

const IDLE: NarrationState = {
  status: "idle",
  activeId: null,
  anchorId: null,
  label: "Read aloud",
  error: "",
  speed: DEFAULT_SPEED,
  chunkIndex: 0,
  chunkCount: 0,
  currentText: "",
  currentAnchor: null,
  currentTime: 0,
  duration: 0,
  durationEstimated: true,
  autoFollow: true,
  voiceContext: null,
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

function storedSpeed(): number {
  try {
    const parsed = Number(window.localStorage.getItem(SPEED_STORAGE_KEY));
    return SPEEDS.includes(parsed as (typeof SPEEDS)[number]) ? parsed : DEFAULT_SPEED;
  } catch {
    return DEFAULT_SPEED;
  }
}

let hydrated = false;
export function hydrate(): void {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  let autoFollow = true;
  try {
    autoFollow = window.localStorage.getItem(FOLLOW_STORAGE_KEY) !== "false";
  } catch {
    // A preference must never prevent playback.
  }
  emit({ speed: storedSpeed(), autoFollow });
}

interface Session {
  run: number;
  items: NarrationItem[];
  blobs: Map<number, Promise<Blob>>;
  /** Immutable logical timeline: the seek bar never moves under the pointer. */
  durations: number[];
  /** Actual media durations, used only to translate logical offsets. */
  mediaDurations: Array<number | null>;
}

let session: Session | null = null;
let audio: HTMLAudioElement | null = null;
let objectUrl: string | null = null;
let generation = 0;
let cursorGeneration = 0;
let controller: AbortController | null = null;

function estimateDuration(text: string): number {
  return Math.max(1.2, text.trim().split(/\s+/).length / 2.75 + 0.25);
}

function effectiveDurations(current: Session): number[] {
  return current.durations;
}

function timeline(current: Session, index: number, itemTime = 0): number {
  return effectiveDurations(current)
    .slice(0, index)
    .reduce((sum, value) => sum + value, itemTime);
}

function refreshDuration(current: Session): void {
  emit({
    duration: current.durations.reduce((sum, value) => sum + value, 0),
    durationEstimated: true,
  });
}

function releaseAudio(): void {
  if (audio) {
    audio.onended = null;
    audio.onerror = null;
    audio.ontimeupdate = null;
    audio.onloadedmetadata = null;
    audio.pause();
    audio.src = "";
  }
  audio = null;
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
  cursorGeneration += 1;
  controller?.abort();
  controller = null;
  session = null;
  releaseAudio();
  stopDeviceVoice();
  emit({
    status: "idle",
    activeId: null,
    anchorId: null,
    chunkIndex: 0,
    chunkCount: 0,
    currentText: "",
    currentAnchor: null,
    currentTime: 0,
    duration: 0,
    durationEstimated: true,
    voiceContext: null,
  });
}

export function pause(): void {
  if (state.status !== "speaking" && state.status !== "preparing") return;
  audio?.pause();
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.pause();
  }
  emit({ status: "paused" });
}

export function resume(): void {
  if (state.status !== "paused") return;
  if (audio) void audio.play();
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.resume();
  }
  emit({ status: "speaking" });
}

export function setSpeed(speed: number): void {
  if (!SPEEDS.includes(speed as (typeof SPEEDS)[number])) return;
  emit({ speed });
  if (audio) audio.playbackRate = speed;
  try {
    window.localStorage.setItem(SPEED_STORAGE_KEY, String(speed));
  } catch {
    // Remembering the preference is optional.
  }
}

export function setAutoFollow(autoFollow: boolean): void {
  emit({ autoFollow });
  try {
    window.localStorage.setItem(FOLLOW_STORAGE_KEY, String(autoFollow));
  } catch {
    // Remembering the preference is optional.
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
  if (!response.ok) return {};
  const payload = (await response.json()) as { descriptions?: Record<string, string> };
  return Object.fromEntries(
    Object.entries(payload.descriptions ?? {}).map(([id, text]) => [Number(id), text]),
  );
}

async function fetchItem(text: string, signal: AbortSignal): Promise<Blob> {
  let lastError = "the reading voice is unavailable";
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}/narration/speech`, {
        method: "POST",
        signal,
        headers: await authorizedHeaders(),
        body: JSON.stringify({ text }),
      });
      if (response.ok) return response.blob();
      lastError = await errorDetail(response);
    } catch (failure) {
      if (signal.aborted) throw failure;
      lastError = failure instanceof Error ? failure.message : lastError;
    }
    if (attempt === 0) {
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(resolve, 250);
        signal.addEventListener("abort", () => {
          window.clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true });
      });
    }
  }
  throw new Error(lastError);
}

function itemBlob(current: Session, index: number, signal: AbortSignal): Promise<Blob> {
  const existing = current.blobs.get(index);
  if (existing) return existing;
  const request = fetchItem(current.items[index]!.speechText, signal);
  request.catch(() => undefined);
  current.blobs.set(index, request);
  return request;
}

function speakOnDevice(items: NarrationItem[], run: number): boolean {
  if (
    typeof window === "undefined" ||
    !("speechSynthesis" in window) ||
    typeof SpeechSynthesisUtterance === "undefined"
  ) return false;
  const utterance = new SpeechSynthesisUtterance(items.map((item) => item.speechText).join(" "));
  const voices = window.speechSynthesis.getVoices();
  utterance.voice =
    voices.find((voice) => voice.lang.toLowerCase().startsWith("en") &&
      /(premium|enhanced|natural|google|microsoft|samantha)/i.test(voice.name)) ??
    voices.find((voice) => voice.lang.toLowerCase().startsWith("en")) ?? null;
  utterance.rate = state.speed;
  utterance.onend = () => {
    if (run === generation) stop();
  };
  window.speechSynthesis.speak(utterance);
  emit({ status: "speaking" });
  return true;
}

function playAudio(
  blob: Blob,
  current: Session,
  index: number,
  offset: number,
  cursor: number,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (current.run !== generation || cursor !== cursorGeneration) {
      resolve();
      return;
    }
    releaseAudio();
    objectUrl = URL.createObjectURL(blob);
    const element = new Audio(objectUrl);
    audio = element;
    element.playbackRate = state.speed;
    // Explicit because older Safari versions otherwise change pitch when the
    // user changes speed, which is commonly perceived as a robotic voice.
    element.preservesPitch = true;

    const adoptMetadata = () => {
      if (Number.isFinite(element.duration) && element.duration > 0) {
        current.mediaDurations[index] = element.duration;
      }
      if (offset > 0) {
        const logicalDuration = current.durations[index] ?? 1;
        const mediaDuration = element.duration || current.mediaDurations[index] || logicalDuration;
        const mediaOffset = (Math.min(offset, logicalDuration) / logicalDuration) * mediaDuration;
        try { element.currentTime = mediaOffset; }
        catch { /* Some engines reject seeking until metadata is ready. */ }
      }
    };
    element.onloadedmetadata = adoptMetadata;
    element.ontimeupdate = () => {
      if (current.run !== generation || cursor !== cursorGeneration) return;
      const logicalDuration = current.durations[index] ?? 1;
      const mediaDuration = current.mediaDurations[index] || element.duration || logicalDuration;
      const logicalTime = Math.min(
        logicalDuration,
        ((element.currentTime || 0) / mediaDuration) * logicalDuration,
      );
      emit({ currentTime: timeline(current, index, logicalTime) });
    };
    element.onended = () => resolve();
    element.onerror = () => reject(new Error("This passage could not be played."));
    adoptMetadata();
    element.play().then(() => {
      if (current.run !== generation || cursor !== cursorGeneration) return;
      if (state.status === "paused") {
        element.pause();
      } else {
        emit({ status: "speaking" });
      }
    }, reject);
  });
}

async function playFrom(index: number, offset = 0): Promise<void> {
  const current = session;
  const signal = controller?.signal;
  if (!current || !signal) return;
  const cursor = ++cursorGeneration;
  releaseAudio();

  for (let at = index; at < current.items.length; at += 1) {
    if (current.run !== generation || cursor !== cursorGeneration) return;
    const item = current.items[at]!;
    emit({
      status: state.status === "paused" ? "paused" : "preparing",
      chunkIndex: at,
      currentText: item.text,
      currentAnchor: item.anchor ?? null,
      currentTime: timeline(current, at, at === index ? offset : 0),
    });
    const blobRequest = itemBlob(current, at, signal);
    if (at + 1 < current.items.length) void itemBlob(current, at + 1, signal);
    if (at + 2 < current.items.length) void itemBlob(current, at + 2, signal);
    try {
      const blob = await blobRequest;
      if (current.run !== generation || cursor !== cursorGeneration) return;
      await playAudio(blob, current, at, at === index ? offset : 0, cursor);
    } catch (failure) {
      if (signal.aborted || current.run !== generation || cursor !== cursorGeneration) return;
      if (speakOnDevice(current.items.slice(at), current.run)) {
        emit({ error: "The natural voice is unavailable, so this is your browser's own voice." });
        return;
      }
      emit({
        status: "paused",
        error: failure instanceof Error && failure.message
          ? failure.message
          : "This answer could not be read aloud.",
      });
      return;
    }
  }
  if (current.run === generation && cursor === cursorGeneration) stop();
}

export async function play(
  id: string,
  input: NarrationInput,
  options: PlaybackOptions = {},
): Promise<void> {
  stop();
  const run = generation;
  controller = new AbortController();
  const { signal } = controller;
  emit({
    status: "preparing",
    activeId: id,
    anchorId: options.anchorId ?? id,
    label: options.label ?? "Read aloud",
    error: "",
    chunkIndex: 0,
    chunkCount: 0,
    currentText: "",
    currentAnchor: null,
    currentTime: 0,
    duration: 0,
    voiceContext: options.voiceContext ?? null,
  });

  let items: NarrationItem[];
  try {
    const descriptions = await fetchDescriptions(input, signal);
    if (run !== generation) return;
    items = narrationItems(buildNarrationScript({ ...input, descriptions }));
  } catch {
    if (signal.aborted || run !== generation) return;
    items = narrationItems(buildNarrationScript(input));
  }
  if (items.length === 0) {
    emit({ status: "idle", activeId: null, error: "There is nothing to read here." });
    return;
  }

  session = {
    run,
    items,
    blobs: new Map(),
    durations: items.map((item) => estimateDuration(item.text)),
    mediaDurations: items.map(() => null),
  };
  emit({ chunkCount: items.length, currentText: items[0]!.text });
  refreshDuration(session);
  await playFrom(0);
}

export async function toggle(
  id: string,
  input: NarrationInput,
  options: PlaybackOptions = {},
): Promise<void> {
  if (state.activeId === id) {
    if (state.status === "speaking" || state.status === "preparing") return pause();
    if (state.status === "paused") return resume();
  }
  await play(id, input, options);
}

export function seekTo(seconds: number): void {
  const current = session;
  if (!current) return;
  const target = Math.max(0, Math.min(seconds, state.duration));
  const durations = effectiveDurations(current);
  let elapsed = 0;
  let index = Math.max(0, durations.length - 1);
  for (let at = 0; at < durations.length; at += 1) {
    if (target <= elapsed + durations[at]!) { index = at; break; }
    elapsed += durations[at]!;
  }
  const wasPaused = state.status === "paused";
  emit({ status: wasPaused ? "paused" : "preparing" });
  void playFrom(index, Math.max(0, target - elapsed)).then(() => {
    if (wasPaused && audio) audio.pause();
  });
}

export function seekBy(seconds: number): void {
  seekTo(state.currentTime + seconds);
}

export function next(): void {
  const current = session;
  if (!current) return;
  void playFrom(Math.min(current.items.length - 1, state.chunkIndex + 1));
}

export function previous(): void {
  const current = session;
  if (!current) return;
  const intoItem = state.currentTime - timeline(current, state.chunkIndex);
  void playFrom(intoItem > 2 ? state.chunkIndex : Math.max(0, state.chunkIndex - 1));
}

/** Test seam: forget playback without relying on media DOM support. */
export function reset(): void {
  generation += 1;
  cursorGeneration += 1;
  controller = null;
  session = null;
  audio = null;
  objectUrl = null;
  hydrated = false;
  state = IDLE;
  for (const listener of listeners) listener();
}
