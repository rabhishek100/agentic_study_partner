"use client";

import { ArrowUp, BookOpen, X } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const MAX_TEXTAREA_HEIGHT_PX = 160;

export interface PageComposerProps {
  /** The page the reader is on, and the thing the chip names. */
  page: number;
  /** The section that page belongs to, when the outline knows it. */
  sectionTitle?: string | null;
  /** False once the reader has dismissed the chip for this question. */
  pageInContext: boolean;
  onPageInContextChange: (inContext: boolean) => void;
  disabled?: boolean;
  onSubmit: (question: string) => void;
}

/**
 * The composer under the document, and the ambient chip above it.
 *
 * The chip is the whole gesture of source-first study. Without it a reader has
 * to select something before every question, which is the tax the mode exists
 * to remove: they are already looking at the page, and saying so in words is
 * work the interface can do for them.
 *
 * It is removable because "ignore the page, answer generally" is a real thing
 * to want, and having to leave the reader to ask it would be worse than a
 * chip with an X on it.
 */
export function PageComposer({
  page,
  sectionTitle,
  pageInContext,
  onPageInContextChange,
  disabled = false,
  onSubmit,
}: PageComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Grow with the question up to a bound, then scroll inside the field.
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
    if (!question || disabled) return;
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
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {pageInContext ? (
          <div className="flex items-center gap-2 rounded-full bg-citation-muted py-1 pl-3 pr-1 text-citation">
            <BookOpen aria-hidden className="size-3.5 shrink-0" />
            <span className="font-mono text-xs tabular-nums">p. {page}</span>
            {sectionTitle && (
              <span className="max-w-56 truncate text-xs">{sectionTitle}</span>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="rounded-full text-citation hover:bg-transparent"
              aria-label={`Stop including page ${page} with this question`}
              onClick={() => onPageInContextChange(false)}
            >
              <X aria-hidden />
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onPageInContextChange(true)}
          >
            <BookOpen aria-hidden />
            Include page {page}
          </Button>
        )}
        <p className="text-xs text-muted-foreground">
          {pageInContext
            ? "This page is in context"
            : "Answering without the page"}
        </p>
      </div>

      <label className="sr-only" htmlFor="page-question">
        Ask about this page
      </label>
      <Textarea
        ref={textareaRef}
        id="page-question"
        rows={1}
        value={value}
        disabled={disabled}
        placeholder={
          pageInContext ? "Ask about this page…" : "Ask anything about this book…"
        }
        className={cn(
          "max-h-[160px] resize-none rounded-xl bg-card py-3 pl-4 pr-14 text-xs shadow-sm",
        )}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="absolute bottom-2 right-2">
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
