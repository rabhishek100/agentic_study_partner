"use client";

import { ArrowUp, Clock, Scissors, X } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";

import { MicButton } from "@/components/dictation/mic-button";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { spliceTranscript } from "@/lib/dictation";
import { timecode } from "@/lib/timecode";

const MAX_TEXTAREA_HEIGHT_PX = 160;

export interface Stretch {
  startMs: number;
  endMs: number;
}

export interface MomentComposerProps {
  /** Where the playhead is, in milliseconds. */
  atMs: number;
  /** A span the viewer marked, which outranks the playhead while it exists. */
  stretch: Stretch | null;
  onMarkStretch: () => void;
  onClearStretch: () => void;
  momentInContext: boolean;
  onMomentInContextChange: (inContext: boolean) => void;
  disabled?: boolean;
  onSubmit: (question: string) => void;
}

/**
 * The composer under the player, and the ambient chip above it.
 *
 * The lecture counterpart of the page chip, and the same argument: a viewer
 * watching at 12:04 should not have to describe in words the thing they are
 * looking at. What differs is what "here" means — a moment reaches backwards
 * further than forwards, because a question asked at 12:04 is nearly always
 * about what was just said, and a viewer who wants a specific span can mark
 * one instead.
 */
export function MomentComposer({
  atMs,
  stretch,
  onMarkStretch,
  onClearStretch,
  momentInContext,
  onMomentInContextChange,
  disabled = false,
  onSubmit,
}: MomentComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(
      textarea.scrollHeight,
      MAX_TEXTAREA_HEIGHT_PX,
    )}px`;
  }, [value]);

  /**
   * Put spoken words where the caret is and leave them there.
   *
   * Read from the textarea's own selection rather than tracked state, because
   * clicking the mic moves focus away and the field keeps the selection it had
   * when it lost it. The same arrangement the main composer uses.
   */
  function insertDictation(transcript: string) {
    const textarea = textareaRef.current;
    const start = textarea?.selectionStart ?? value.length;
    const end = textarea?.selectionEnd ?? start;
    const spliced = spliceTranscript(value, transcript, start, end);
    setValue(spliced.value);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(spliced.caret, spliced.caret);
    });
  }

  function submit() {
    const question = value.trim();
    if (!question || disabled) return;
    onSubmit(question);
    setValue("");
  }

  const label = stretch
    ? `${timecode(stretch.startMs)} – ${timecode(stretch.endMs)}`
    : timecode(atMs);

  return (
    <form
      className="relative"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {momentInContext ? (
          <div className="flex items-center gap-2 rounded-full bg-citation-muted py-1 pl-3 pr-1 text-citation">
            <Clock aria-hidden className="size-3.5 shrink-0" />
            <span className="font-mono text-xs tabular-nums">{label}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="rounded-full text-citation hover:bg-transparent"
              aria-label={`Stop including ${label} with this question`}
              onClick={() => onMomentInContextChange(false)}
            >
              <X aria-hidden />
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onMomentInContextChange(true)}
          >
            <Clock aria-hidden />
            Include {label}
          </Button>
        )}

        {stretch ? (
          <Button type="button" variant="ghost" size="sm" onClick={onClearStretch}>
            <X aria-hidden />
            Clear the marked stretch
          </Button>
        ) : (
          <Button type="button" variant="ghost" size="sm" onClick={onMarkStretch}>
            <Scissors aria-hidden />
            Mark a stretch from here
          </Button>
        )}

        <p className="text-xs text-muted-foreground">
          {!momentInContext
            ? "Answering without the lecture's position"
            : stretch
              ? "This stretch is in context"
              : "This moment and what is on screen are in context"}
        </p>
      </div>

      <label className="sr-only" htmlFor="moment-question">
        Ask about this moment
      </label>
      <Textarea
        ref={textareaRef}
        id="moment-question"
        rows={1}
        value={value}
        disabled={disabled}
        placeholder={
          momentInContext
            ? "Ask about this moment…"
            : "Ask anything about this lecture…"
        }
        className="max-h-[160px] resize-none rounded-xl bg-card py-3 pl-4 pr-20 text-xs shadow-sm max-md:text-base"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="absolute bottom-2 right-2 flex items-center gap-1">
        <MicButton
          disabled={disabled}
          size="icon-sm"
          onTranscript={insertDictation}
          label={`Dictate a question`}
        />
        <Button
          type="submit"
          size="icon-sm"
          aria-label="Ask this question"
          disabled={disabled || !value.trim()}
        >
          <ArrowUp aria-hidden />
        </Button>
      </div>
    </form>
  );
}
