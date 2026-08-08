"use client";

import { ArrowUp, Square } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { ResponseDepth } from "@/lib/types";

/**
 * Room for about three lines before scrolling.
 *
 * A side question is short, but the first version gave it a single 36px line,
 * which made typing anything longer than a few words feel like writing in a
 * slot. The floor matters more than the ceiling here.
 */
const MIN_TEXTAREA_HEIGHT_PX = 60;
const MAX_TEXTAREA_HEIGHT_PX = 168;

export interface SideChatComposerProps {
  isStreaming: boolean;
  responseDepth: ResponseDepth;
  onResponseDepthChange: (depth: ResponseDepth) => void;
  onSubmit: (question: string) => void;
  onStop: () => void;
  /** Hidden when this surface's turns have no depth to choose. */
  showDepth?: boolean;
  /** Labels the controls with the thread they belong to, for screen readers. */
  label: string;
}

/**
 * The small composer inside a side chat window.
 *
 * A deliberately reduced version of the main composer: no `@book` mentions,
 * because a side chat's scope is inherited from its parent and frozen, and no
 * prompt settings, because it uses the parent's profile. What it keeps is the
 * depth selector, which defaults to Quick here — the window is small and the
 * question is usually a clarification.
 */
export function SideChatComposer({
  isStreaming,
  responseDepth,
  onResponseDepthChange,
  onSubmit,
  onStop,
  showDepth = true,
  label,
}: SideChatComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  function resize() {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(
      Math.max(textarea.scrollHeight, MIN_TEXTAREA_HEIGHT_PX),
      MAX_TEXTAREA_HEIGHT_PX,
    )}px`;
  }

  function submit() {
    const question = value.trim();
    if (!question || isStreaming) return;
    onSubmit(question);
    setValue("");
    requestAnimationFrame(resize);
  }

  return (
    <div className="shrink-0 border-t border-border bg-background p-2.5">
      {/*
        One bordered field containing the input and its controls, rather than a
        small input with buttons floating beside it: at this width every pixel
        of horizontal room belongs to the text being typed.
      */}
      <div className="rounded-xl border border-input bg-transparent transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 dark:bg-input/30">
        <Textarea
          ref={textareaRef}
          value={value}
          rows={2}
          aria-label={`Ask a question in the ${label} side chat`}
          placeholder="Ask about this…"
          style={{ minHeight: MIN_TEXTAREA_HEIGHT_PX }}
          className="side-chat-ui resize-none rounded-none border-0 bg-transparent px-2.5 py-2 leading-snug shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
          onChange={(event) => {
            setValue(event.target.value);
            resize();
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <div className="flex items-center justify-between gap-1 px-1.5 pb-1.5">
          {showDepth ? (
          <Select
            value={responseDepth}
            onValueChange={(depth) =>
              onResponseDepthChange(depth as ResponseDepth)
            }
          >
            <SelectTrigger
              size="sm"
              className="side-chat-ui h-7 border-0 bg-transparent px-1.5 text-muted-foreground shadow-none"
              aria-label={`Answer depth for the ${label} side chat`}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="quick">Quick answer</SelectItem>
              <SelectItem value="interview">Interview answer</SelectItem>
              <SelectItem value="deep">Deep dive</SelectItem>
            </SelectContent>
          </Select>
          ) : (
            // Keeps the send button where it always is, rather than letting it
            // jump to the left edge on the surface without a depth control.
            <span />
          )}

          {isStreaming ? (
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="Stop generating this answer"
              onClick={onStop}
            >
              <Square aria-hidden />
            </Button>
          ) : (
            <Button
              size="icon-sm"
              aria-label="Send this question"
              disabled={!value.trim()}
              onClick={submit}
            >
              <ArrowUp aria-hidden />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
