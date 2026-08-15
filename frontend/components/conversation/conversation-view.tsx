"use client";

import { ArrowDown } from "lucide-react";
import { useLayoutEffect } from "react";

import { Composer } from "@/components/conversation/composer";
import { PromptSettings } from "@/components/conversation/prompt-settings";
import { TurnView } from "@/components/conversation/turn-view";
import { Welcome } from "@/components/conversation/welcome";
import { AskSelection } from "@/components/side-chat/ask-selection";
import { Button } from "@/components/ui/button";
import { useScrollAnchor } from "@/hooks/use-scroll-anchor";
import type {
  BookSummary,
  ChatTurn,
  EvidenceRef,
  ResponseDepth,
} from "@/lib/types";

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
  selectedBookIds?: number[];
  canSend: boolean;
  onSend: (question: string, mentionedBookIds?: number[]) => void;
  onStop: () => void;
  onRetry: () => void;
  conversationId: string | null;
  responseDepth: ResponseDepth;
  onResponseDepthChange: (depth: ResponseDepth) => void;
  books: BookSummary[];
  documentType?: "book" | "paper";
  /** What the next question will search, e.g. "All 3 books". */
  scopeSummary?: string | null;
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
  onAskOnTheSide?: (turnIndex: number, quotedText: string) => void;
}

export function ConversationView({
  turns,
  isStreaming,
  hasBooks,
  selectedBookIds = [],
  canSend,
  onSend,
  onStop,
  onRetry,
  conversationId,
  responseDepth,
  onResponseDepthChange,
  books,
  documentType = "book",
  scopeSummary,
  onOpenReference,
  onAskOnTheSide,
}: ConversationViewProps) {
  const { viewportRef, contentRef, isPinned, scrollToBottom } = useScrollAnchor<
    HTMLDivElement,
    HTMLDivElement
  >();

  const isEmpty = turns.length === 0;

  // A resumed conversation is a new scroll context even though the same DOM
  // viewport is reused. Without this, switching from a conversation that was
  // scrolled halfway up could open the next one at that arbitrary offset.
  useLayoutEffect(() => {
    scrollToBottom("auto");
  }, [conversationId, scrollToBottom]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="sr-only" role="status" aria-live="polite">
        {liveStatus(turns)}
      </p>

      <div
        ref={viewportRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
      >
        <div
          ref={contentRef}
          className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-6 sm:px-6"
        >
          {isEmpty ? (
            <Welcome
              hasBooks={hasBooks}
              selectedBookIds={selectedBookIds}
              canUseStarters={canSend}
              documentType={documentType}
              onAsk={onSend}
            />
          ) : (
            turns.map((turn, index) => (
              <TurnView
                key={turn.id}
                turn={turn}
                isLast={index === turns.length - 1}
                canRetry={hasBooks && !isStreaming}
                onRetry={onRetry}
                onOpenReference={onOpenReference}
                onAskOnTheSide={onAskOnTheSide}
              />
            ))
          )}
        </div>
      </div>

      {/* Offers to open a side chat on whatever passage the reader highlights. */}
      {onAskOnTheSide && (
        <AskSelection container={contentRef} onAsk={onAskOnTheSide} />
      )}

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
            disabled={!hasBooks}
            isStreaming={isStreaming}
            placeholder={
              hasBooks
                ? `Ask about the ${documentType}…`
                : `Upload a ${documentType} first…`
            }
            label={`Ask about the ${documentType}`}
            onSubmit={onSend}
            onStop={onStop}
            books={books}
            hasDefaultScope={canSend}
            responseDepth={responseDepth}
            onResponseDepthChange={onResponseDepthChange}
            settingsControl={
              <PromptSettings
                conversationId={conversationId}
                responseDepth={responseDepth}
              />
            }
          />
          <p className="mt-2 text-center text-[0.7rem] text-muted-foreground">
            {!canSend && hasBooks
              ? `No default scope — type @ to tag a ${documentType} for this question.`
              : scopeSummary
              ? `Answers are limited to evidence found in ${scopeSummary.toLowerCase()}.`
              : `Answers are limited to the evidence found in your ${documentType === "paper" ? "papers" : "books"}.`}
          </p>
        </div>
      </div>
    </div>
  );
}
