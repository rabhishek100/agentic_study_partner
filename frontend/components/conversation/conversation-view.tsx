"use client";

import { ArrowDown } from "lucide-react";
import { useLayoutEffect } from "react";

import { Composer } from "@/components/conversation/composer";
import { PromptSettingsLink } from "@/components/conversation/prompt-settings-link";
import { RevisionSheets } from "@/components/revision/revision-sheets";
import type { DocumentNoun } from "@/components/book-selector";
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
  /** Which library this is, so the copy can name it. */
  noun?: DocumentNoun;
  /** Points the right region at one turn's sources. */
  onShowSources?: (turnIndex: number) => void;
  /** Which turn the region is currently showing, if any. */
  shownSourcesTurn?: number | null;
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
  /** What the next question will search, e.g. "All 3 books". */
  scopeSummary?: string | null;
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
  onAskOnTheSide?: (turnIndex: number, quotedText: string) => void;
}

export function ConversationView({
  turns,
  noun = "book",
  onShowSources,
  shownSourcesTurn,
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

      {/*
        With nothing asked yet there is nothing to scroll back to, so the empty
        state fits itself to the pane instead of scrolling.
      */}
      <div
        ref={viewportRef}
        className={`min-h-0 flex-1 overscroll-contain [scrollbar-gutter:stable] ${
          isEmpty ? "overflow-hidden" : "overflow-y-auto"
        }`}
      >
        <div
          ref={contentRef}
          className={`mx-auto flex w-full max-w-3xl flex-col px-4 sm:px-6 ${
            isEmpty ? "h-full" : "gap-8 py-6"
          }`}
        >
          {isEmpty ? (
            <Welcome
              hasBooks={hasBooks}
              selectedBookIds={selectedBookIds}
              canUseStarters={canSend}
              onAsk={onSend}
            />
          ) : (
            turns.map((turn, index) => (
              <TurnView
                key={turn.id}
                turn={turn}
                conversationId={conversationId}
                isLast={index === turns.length - 1}
                canRetry={hasBooks && !isStreaming}
                onRetry={onRetry}
                onOpenReference={onOpenReference}
                onShowSources={
                  onShowSources && turn.turnIndex != null
                    ? () => onShowSources(turn.turnIndex!)
                    : undefined
                }
                sourcesShown={
                  turn.turnIndex != null && turn.turnIndex === shownSourcesTurn
                }
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
            noun={noun}
            disabled={!hasBooks}
            isStreaming={isStreaming}
            placeholder={
              hasBooks ? `Ask about the ${noun}…` : `Upload a ${noun} first…`
            }
            onSubmit={onSend}
            onStop={onStop}
            books={books}
            hasDefaultScope={canSend}
            responseDepth={responseDepth}
            onResponseDepthChange={onResponseDepthChange}
            settingsControl={
              <div className="flex flex-wrap items-center gap-1">
                <RevisionSheets books={books} selectedBookIds={selectedBookIds} noun={noun} />
                <PromptSettingsLink
                  conversationId={conversationId}
                  responseDepth={responseDepth}
                />
              </div>
            }
          />
          <p className="mt-2 text-center text-xs text-muted-foreground">
            {!canSend && hasBooks
              ? "No default scope — type @ to tag a book for this question."
              : scopeSummary
              ? `Answers are limited to evidence found in ${scopeSummary.toLowerCase()}.`
              : `Answers are limited to the evidence found in your ${noun}s.`}
          </p>
        </div>
      </div>
    </div>
  );
}
