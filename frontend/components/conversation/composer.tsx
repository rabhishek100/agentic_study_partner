"use client";

import { ArrowUp, Square } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

const MAX_TEXTAREA_HEIGHT_PX = 200;

export interface ComposerProps {
  disabled: boolean;
  isStreaming: boolean;
  placeholder: string;
  onSubmit: (question: string) => void;
  onStop: () => void;
}

export function Composer({
  disabled,
  isStreaming,
  placeholder,
  onSubmit,
  onStop,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Grow with the question up to a bound, then scroll inside the field. A
  // fixed two-row box hides the end of anything longer than a sentence.
  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(
      textarea.scrollHeight,
      MAX_TEXTAREA_HEIGHT_PX,
    )}px`;
  }, [value]);

  function submit() {
    const question = value.trim();
    if (!question || disabled || isStreaming) return;
    onSubmit(question);
    setValue("");
  }

  return (
    <form
      className="relative"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label className="sr-only" htmlFor="question">
        Ask about the book
      </label>
      <Textarea
        ref={textareaRef}
        id="question"
        rows={1}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder}
        disabled={disabled}
        className="max-h-[200px] resize-none rounded-xl bg-card py-3 pl-3.5 pr-13 text-[0.95rem] shadow-sm"
      />

      <div className="absolute bottom-2 right-2">
        {isStreaming ? (
          <Button
            type="button"
            size="icon-sm"
            variant="secondary"
            onClick={onStop}
            aria-label="Stop generating"
          >
            <Square aria-hidden />
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon-sm"
            disabled={disabled || !value.trim()}
            aria-label="Send question"
          >
            <ArrowUp aria-hidden />
          </Button>
        )}
      </div>
    </form>
  );
}
