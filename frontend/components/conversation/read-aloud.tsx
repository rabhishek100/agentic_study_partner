"use client";

import { Loader2, Pause, Play, Volume2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useReadAloud } from "@/hooks/use-read-aloud";
import type { NarrationInput } from "@/lib/narration";
import type { NarrationVoiceContext } from "@/lib/narration-player";

export interface ReadAloudProps {
  /** Identifies this control's passage, so only the speaking one looks active. */
  id: string;
  /** What to read. Cheap to build; the script is assembled on play. */
  source: () => NarrationInput;
  /** Overrides the label where "Read aloud" would be ambiguous. */
  label?: string;
  size?: "xs" | "sm";
  /** Answer body to highlight when it is not the same as this control's id. */
  anchorId?: string;
  voiceContext?: NarrationVoiceContext;
}

/**
 * Play, pause and stop one passage, with the speed the reader chose.
 *
 * The transport only appears once this control is the one speaking. A row of
 * turns each showing a stop button and a speed menu would be a wall of
 * controls for a thing that happens one at a time.
 */
export function ReadAloud({
  id,
  source,
  label = "Read aloud",
  size = "xs",
  anchorId,
  voiceContext,
}: ReadAloudProps) {
  const narration = useReadAloud();
  const isActive = narration.activeId === id;
  const isPreparing = isActive && narration.status === "preparing";
  const isSpeaking = isActive && narration.status === "speaking";
  const isPaused = isActive && narration.status === "paused";

  const announcement = !isActive
    ? ""
    : narration.error
    ? narration.error
    : isSpeaking && narration.chunkCount > 0
    ? `Reading, passage ${narration.chunkIndex + 1} of ${narration.chunkCount}.`
    : isPaused
    ? "Reading paused."
    : "";

  return (
    <span className="inline-flex items-center gap-1">
      <Button
        variant="ghost"
        size={size}
        onClick={() =>
          void narration.toggle(id, source(), { anchorId, label, voiceContext })
        }
        aria-label={
          isSpeaking ? `Pause ${label.toLowerCase()}` : isPaused ? "Resume reading" : label
        }
      >
        {isPreparing ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : isSpeaking ? (
          <Pause aria-hidden />
        ) : isPaused ? (
          <Play aria-hidden />
        ) : (
          <Volume2 aria-hidden />
        )}
        {isPreparing ? "Preparing…" : isSpeaking ? "Pause" : isPaused ? "Resume" : label}
      </Button>

      {/*
        Progress and failures are announced rather than drawn: the reader
        listening to this is, by definition, not watching the control.

        Rendered only when there is something to say. An always-present empty
        live region announces nothing and costs something: every surface that
        already has a status region would have two, and "the status" would stop
        meaning one thing.
      */}
      {announcement && (
        <span className="sr-only" role="status" aria-live="polite">
          {announcement}
        </span>
      )}
    </span>
  );
}

/** The failure banner, for a surface that wants it visible as well as spoken. */
export function ReadAloudError({ id }: { id: string }) {
  const narration = useReadAloud();
  if (narration.activeId !== id || !narration.error) return null;
  return (
    <p className="text-xs text-muted-foreground">{narration.error}</p>
  );
}
