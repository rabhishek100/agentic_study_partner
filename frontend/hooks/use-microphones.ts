"use client";

import { useSyncExternalStore } from "react";

import {
  getMicrophoneSnapshot,
  getServerMicrophoneSnapshot,
  selectMicrophone,
  subscribeMicrophones,
  type MicrophoneSnapshot,
} from "@/lib/microphone";

export interface Microphones extends MicrophoneSnapshot {
  select(deviceId: string | null): void;
}

/**
 * The microphones this browser can record from, and which one is chosen.
 *
 * Backed by a module-level store so every mounted composer shows the same
 * choice, and so the list updates for all of them when a headset is plugged
 * in or pulled out mid-session.
 */
export function useMicrophones(): Microphones {
  const snapshot = useSyncExternalStore(
    subscribeMicrophones,
    getMicrophoneSnapshot,
    getServerMicrophoneSnapshot,
  );
  return { ...snapshot, select: selectMicrophone };
}
