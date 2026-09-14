"use client";

import { useEffect, useSyncExternalStore } from "react";

import {
  hydrate,
  next,
  pause,
  play,
  playScript,
  previous,
  resume,
  seekBy,
  seekTo,
  setAutoFollow,
  setPacing,
  setSpeed,
  snapshot,
  stop,
  subscribe,
  suspendAutoFollow,
  toggle,
  type NarrationState,
} from "@/lib/narration-player";

export interface ReadAloud extends NarrationState {
  play: typeof play;
  playScript: typeof playScript;
  toggle: typeof toggle;
  pause: typeof pause;
  resume: typeof resume;
  stop: typeof stop;
  setSpeed: typeof setSpeed;
  setPacing: typeof setPacing;
  setAutoFollow: typeof setAutoFollow;
  suspendAutoFollow: typeof suspendAutoFollow;
  seekTo: typeof seekTo;
  seekBy: typeof seekBy;
  next: typeof next;
  previous: typeof previous;
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

  return {
    ...state,
    play,
    playScript,
    toggle,
    pause,
    resume,
    stop,
    setSpeed,
    setPacing,
    setAutoFollow,
    suspendAutoFollow,
    seekTo,
    seekBy,
    next,
    previous,
  };
}
