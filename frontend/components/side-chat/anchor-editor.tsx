"use client";

import { Plus, Quote, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { MAXIMUM_ANCHORS } from "@/hooks/use-side-chats";
import type { QuoteAnchor } from "@/lib/types";

export interface AnchorEditorProps {
  anchors: QuoteAnchor[];
  onChange: (anchors: QuoteAnchor[]) => void;
  /**
   * Which recorded turn a pasted passage came from, or null when it came from
   * somewhere else entirely. Resolved against the parent conversation rather
   * than assumed, so a pasted reference names the turn that actually contains
   * it and its citation markers resolve against the right evidence.
   */
  resolveTurn: (text: string) => number | null;
}

/**
 * The passages a side chat is anchored to, and the controls to change them.
 *
 * Two ways in, matching the two ways a reader works: highlight a passage in the
 * main conversation while this window is focused, or paste one here. Pasting is
 * explicit rather than inferred from what lands in the composer — a long paste
 * into a question box is sometimes a long question.
 */
export function AnchorEditor({
  anchors,
  onChange,
  resolveTurn,
}: AnchorEditorProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState("");
  const full = anchors.length >= MAXIMUM_ANCHORS;

  function add() {
    const text = draft.trim();
    if (!text) return;
    const turnIndex = resolveTurn(text);
    if (turnIndex === null) {
      setProblem(
        "That passage is not from this conversation, so there is nothing to " +
          "anchor it to. Copy it from an answer above.",
      );
      return;
    }
    onChange([
      ...anchors,
      {
        // The server assigns the real id; this one only has to be unique on
        // screen until it answers.
        anchor_id: `pending-${crypto.randomUUID()}`,
        parent_turn_index: turnIndex,
        quoted_text: text,
      },
    ]);
    setDraft("");
    setProblem("");
    setOpen(false);
  }

  return (
    <div className="shrink-0 border-b border-border bg-muted/30 px-3 py-2">
      {anchors.length > 0 && (
        <ul className="space-y-1.5">
          {anchors.map((anchor) => (
            <li key={anchor.anchor_id} className="group flex gap-1.5">
              <Quote
                aria-hidden
                className="mt-[0.2em] size-[1em] shrink-0 text-muted-foreground"
              />
              <blockquote className="side-chat-ui line-clamp-3 flex-1 italic leading-snug text-muted-foreground">
                {anchor.quoted_text}
              </blockquote>
              <Button
                variant="ghost"
                size="icon-sm"
                className="size-5 shrink-0 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                aria-label={`Remove the reference beginning "${anchor.quoted_text.slice(0, 40)}"`}
                onClick={() =>
                  onChange(
                    anchors.filter((item) => item.anchor_id !== anchor.anchor_id),
                  )
                }
              >
                <X aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {anchors.length === 0 && (
        <p className="side-chat-ui text-muted-foreground">
          No passage attached. This side chat still searches the same books.
        </p>
      )}

      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setProblem("");
        }}
      >
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="xs"
            className="side-chat-ui mt-1 h-auto py-1 text-muted-foreground"
            disabled={full}
            title={
              full
                ? `A side chat can reference at most ${MAXIMUM_ANCHORS} passages`
                : undefined
            }
          >
            <Plus aria-hidden />
            Add a passage
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80 space-y-2">
          <p className="text-xs text-muted-foreground">
            Paste a passage from this conversation to reference it here.
          </p>
          <Textarea
            autoFocus
            rows={4}
            value={draft}
            aria-label="Passage to reference"
            className="resize-none text-xs"
            onChange={(event) => {
              setDraft(event.target.value);
              setProblem("");
            }}
          />
          {problem && (
            <p className="text-xs text-destructive" role="alert">
              {problem}
            </p>
          )}
          <div className="flex justify-end gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" disabled={!draft.trim()} onClick={add}>
              Add reference
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
