"use client";

import { ChevronDown, Loader2, Mic, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useDictation } from "@/hooks/use-dictation";
import { useMicrophones } from "@/hooks/use-microphones";
import { canDictate, recordingClock } from "@/lib/dictation";
import { cn } from "@/lib/utils";

/** Stands in for "whatever the system is set to", which has no device id. */
const SYSTEM_DEFAULT = "system-default";

export interface MicButtonProps {
  /** Called with the words that were spoken, for the composer to place. */
  onTranscript: (text: string) => void;
  disabled?: boolean;
  /** The button's accessible name while idle. */
  label?: string;
  size?: "icon-xs" | "icon-sm" | "icon";
  className?: string;
}

/**
 * Speak a question instead of typing it.
 *
 * One button with three states, in the composer beside send: tap to record,
 * tap again to transcribe. The words land in the question box rather than
 * being asked, so a misheard term is fixed before any retrieval happens.
 *
 * It renders nothing where recording is impossible — an insecure origin, or a
 * browser without `MediaRecorder`. A dead microphone button next to a working
 * text field is worse than no button, and every surface it appears on is
 * fully usable by typing.
 */
export function MicButton({
  onTranscript,
  disabled = false,
  label = "Dictate a question",
  size = "icon-sm",
  className,
}: MicButtonProps) {
  // Resolved after mount: the server has no microphone to ask about, and
  // rendering the button only to remove it would mismatch hydration.
  const [supported, setSupported] = useState(false);
  const { status, error, elapsedMs, start, stop, cancel, dismissError } =
    useDictation(onTranscript);
  const { devices, selectedId, select } = useMicrophones();

  useEffect(() => setSupported(canDictate()), []);

  // Escape is the way out of a recording that was started by accident, and it
  // spends nothing: the audio is dropped rather than transcribed.
  useEffect(() => {
    if (status !== "recording" && status !== "starting") return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      cancel();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [cancel, status]);

  if (!supported) return null;

  const isRecording = status === "recording";
  const isBusy = status === "starting" || status === "transcribing";

  return (
    <span className={cn("relative inline-flex items-center", className)}>
      {/* One slot above the button carries whatever needs saying — how long
          this recording has run, or why the last one produced nothing. Both
          float, so the composer never changes size mid-question. */}
      {(error || isRecording) && (
        <span
          className="absolute bottom-full right-0 z-30 mb-2 w-max max-w-56 rounded-md border border-border bg-popover px-2 py-1 text-xs leading-snug text-popover-foreground shadow-md"
          // Announced by the live region below, so it is decoration here and
          // would otherwise be read twice.
          aria-hidden
        >
          {error ?? (
            <span className="flex items-center gap-2 tabular-nums">
              <span className="size-1.5 shrink-0 rounded-full bg-destructive motion-safe:animate-pulse" />
              {recordingClock(elapsedMs)}
            </span>
          )}
        </span>
      )}

      <p className="sr-only" role="status" aria-live="polite">
        {error ??
          (isRecording
            ? "Recording. Press Escape to discard."
            : status === "transcribing"
              ? "Transcribing."
              : "")}
      </p>

      <Button
        type="button"
        size={size}
        variant={isRecording ? "secondary" : "ghost"}
        disabled={disabled || isBusy}
        aria-label={
          isRecording
            ? "Stop recording and transcribe"
            : status === "transcribing"
              ? "Transcribing your question"
              : label
        }
        aria-pressed={isRecording}
        onClick={() => {
          dismissError();
          if (isRecording) stop();
          else if (!isBusy) start();
        }}
      >
        {isRecording ? (
          <Square
            aria-hidden
            className="fill-destructive text-destructive motion-safe:animate-pulse"
          />
        ) : isBusy ? (
          <Loader2 aria-hidden className="motion-safe:animate-spin" />
        ) : (
          <Mic aria-hidden />
        )}
      </Button>

      {/* Only worth showing once there is a choice to make. Before the first
          recording the browser reports inputs without names, and often only
          one of them, so this appears after permission has been granted —
          which is also the first moment the names are readable. */}
      {devices.length > 1 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              disabled={disabled || isRecording || isBusy}
              aria-label="Choose a microphone"
              className="text-muted-foreground"
            >
              <ChevronDown aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          {/* Fixed width, not a max: device names run long ("Jabra Evolve2 65
              Hands-Free"), and a menu that sizes to its content would be a
              different width every time a headset is plugged in. */}
          <DropdownMenuContent align="end" className="w-64">
            <DropdownMenuLabel>Microphone</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuRadioGroup
              value={selectedId ?? SYSTEM_DEFAULT}
              onValueChange={(value) =>
                select(value === SYSTEM_DEFAULT ? null : value)
              }
            >
              <DropdownMenuRadioItem value={SYSTEM_DEFAULT}>
                <span className="truncate">System default</span>
              </DropdownMenuRadioItem>
              {devices.map((device) => (
                <DropdownMenuRadioItem
                  key={device.deviceId}
                  value={device.deviceId}
                >
                  {/* The title carries the name the width cannot. */}
                  <span className="truncate" title={device.label}>
                    {device.label}
                  </span>
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </span>
  );
}
