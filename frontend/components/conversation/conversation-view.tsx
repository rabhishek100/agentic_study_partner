"use client";

import { ArrowDown } from "lucide-react";

import { Composer } from "@/components/conversation/composer";
import { TurnView } from "@/components/conversation/turn-view";
import { Welcome } from "@/components/conversation/welcome";
import { Button } from "@/components/ui/button";
import { useScrollAnchor } from "@/hooks/use-scroll-anchor";
import type { ChatTurn, EvidenceRef } from "@/lib/types";

/**
 * Announced to assistive technology when a turn settles.
 *
 * Deliberately not the answer text: making the message list a live region
 * would announce every streamed token, which is unusable.
 */
function liveStatus(turns: ChatTurn[]): string {
  const last = turns.at(-1);
  if (!last) return "";
  if (last.status === "streaming") return "Generating an answer.";
  if (last.status === "complete") return "Answer complete.";
  if (last.status === "stopped") return "Answer stopped.";
  return last.error ?? "The request failed.";
}

export interface ConversationViewProps {
  turns: ChatTurn[];
  isStreaming: boolean;
  hasBooks: boolean;
  canSend: boolean;
  onSend: (question: string) => void;
  onStop: () => void;
  onRetry: () => void;
  /** What the next question will search, e.g. "All 3 books". */
  scopeSummary?: string | null;
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
}

export function ConversationView({
  turns,
  isStreaming,
  hasBooks,
  canSend,
  onSend,
  onStop,
  onRetry,
  scopeSummary,
  onOpenReference,
}: ConversationViewProps) {
  const { viewportRef, contentRef, isPinned, scrollToBottom } = useScrollAnchor<
    HTMLDivElement,
    HTMLDivElement
  >();

  const isEmpty = turns.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="sr-only" role="status" aria-live="polite">
        {liveStatus(turns)}
      </p>

      <div
        ref={viewportRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        <div
          ref={contentRef}
          className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-6 sm:px-6"
        >
          {isEmpty ? (
            <Welcome hasBooks={hasBooks} onAsk={onSend} />
          ) : (
            turns.map((turn, index) => (
              <TurnView
                key={turn.id}
                turn={turn}
                isLast={index === turns.length - 1}
                canRetry={canSend && !isStreaming}
                onRetry={onRetry}
                onOpenReference={onOpenReference}
              />
            ))
          )}
        </div>
      </div>

      <div className="relative border-t border-border bg-background">
        {/*
          Sits just above the composer's top edge with a fade behind it. The
          previous `-top-12` floated it over live message content, so it
          collided with whatever happened to be scrolled to that position.
        */}
        {!isPinned && !isEmpty && (
          <div className="pointer-events-none absolute inset-x-0 -top-10 flex h-10 items-end justify-center bg-gradient-to-t from-background to-transparent pb-1">
            <Button
              variant="outline"
              size="sm"
              className="pointer-events-auto shadow-md"
              onClick={() => scrollToBottom()}
            >
              <ArrowDown aria-hidden />
              Jump to latest
            </Button>
          </div>
        )}

        <div className="mx-auto w-full max-w-3xl px-4 py-3 sm:px-6">
          <Composer
            disabled={!canSend}
            isStreaming={isStreaming}
            placeholder={
              hasBooks ? "Ask about the book…" : "Upload a book first…"
            }
            onSubmit={onSend}
            onStop={onStop}
          />
          <p className="mt-2 text-center text-[0.7rem] text-muted-foreground">
            {scopeSummary
              ? `Answers are limited to evidence found in ${scopeSummary.toLowerCase()}.`
              : "Answers are limited to the evidence found in your books."}
          </p>
        </div>
      </div>
    </div>
  );
}
