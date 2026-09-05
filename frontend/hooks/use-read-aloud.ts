"use client";

import { useEffect, useSyncExternalStore } from "react";

import {
  hydrate,
  pause,
  play,
  resume,
  setSpeed,
  snapshot,
  stop,
  subscribe,
  toggle,
  type NarrationState,
} from "@/lib/narration-player";

export interface ReadAloud extends NarrationState {
  play: typeof play;
  toggle: typeof toggle;
  pause: typeof pause;
  resume: typeof resume;
  stop: typeof stop;
  setSpeed: typeof setSpeed;
}

/**
 * The application's single voice, as React state.
 *
 * Subscribed rather than owned: the player is one module-level object, so a
 * control on a turn, a control in a side chat and the one on the selection
 * popover all see the same playback and only one of them is ever active.
 */
export function useReadAloud(): ReadAloud {
  const state = useSyncExternalStore(subscribe, snapshot, snapshot);

  useEffect(() => {
    hydrate();
  }, []);

  return { ...state, play, toggle, pause, resume, stop, setSpeed };
}
