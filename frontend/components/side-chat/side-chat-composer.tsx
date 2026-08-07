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

const MAX_TEXTAREA_HEIGHT_PX = 120;

export interface SideChatComposerProps {
  isStreaming: boolean;
  responseDepth: ResponseDepth;
  onResponseDepthChange: (depth: ResponseDepth) => void;
  onSubmit: (question: string) => void;
  onStop: () => void;
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
  label,
}: SideChatComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  function resize() {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(
      textarea.scrollHeight,
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
    <div className="shrink-0 border-t border-border bg-background p-2">
      <div className="flex items-end gap-1.5">
        <Textarea
          ref={textareaRef}
          value={value}
          rows={1}
          aria-label={`Ask a question in the ${label} side chat`}
          placeholder="Ask about this…"
          className="min-h-9 resize-none py-2 text-xs"
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

      <Select
        value={responseDepth}
        onValueChange={(depth) => onResponseDepthChange(depth as ResponseDepth)}
      >
        <SelectTrigger
          size="sm"
          className="mt-1.5 h-7 border-0 bg-transparent px-1.5 text-[0.7rem] text-muted-foreground shadow-none"
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
    </div>
  );
}
