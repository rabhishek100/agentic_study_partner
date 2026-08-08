"use client";

import { ArrowDown, Loader2, Send, Square } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";

import { VideoTurnView } from "@/components/video/video-turn";
import { AskSelection } from "@/components/side-chat/ask-selection";
import { MicButton } from "@/components/dictation/mic-button";
import { VideoWelcome } from "@/components/video/video-welcome";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useScrollAnchor } from "@/hooks/use-scroll-anchor";
import { spliceTranscript } from "@/lib/dictation";
import type {
  VideoChapter,
  VideoDocumentTarget,
  VideoTurn,
} from "@/lib/video-types";

/**
 * Announced to assistive technology when a turn settles.
 *
 * Deliberately not the answer text: making the message list a live region
 * would announce every streamed token, which is unusable.
 */
function liveStatus(turns: VideoTurn[]): string {
  const last = turns.at(-1);
  if (!last) return "";
  if (last.status === "streaming") return "Generating an answer.";
  if (last.status === "complete") return "Answer complete.";
  if (last.status === "stopped") return "Answer stopped.";
  return last.error ?? "The request failed.";
}

interface AskPaneProps {
  videoId: string;
  turns: VideoTurn[];
  chapters: VideoChapter[];
  isStreaming: boolean;
  canAsk: boolean;
  blockedReason: string | null;
  /** Changes when a different conversation is opened, resetting the scroll. */
  conversationId: string | null;
  onAsk(question: string): void;
  onStop(): void;
  onRetry(): void;
  onSeek(milliseconds: number): void;
  onOpenDocument(target: VideoDocumentTarget): void;
  onAskOnTheSide?: (turnIndex: number, quotedText: string) => void;
}

export function AskPane({
  videoId,
  turns,
  chapters,
  isStreaming,
  canAsk,
  blockedReason,
  conversationId,
  onAsk,
  onStop,
  onRetry,
  onSeek,
  onOpenDocument,
  onAskOnTheSide,
}: AskPaneProps) {
  const [question, setQuestion] = useState("");
  const questionRef = useRef<HTMLTextAreaElement | null>(null);
  const { viewportRef, contentRef, isPinned, scrollToBottom } = useScrollAnchor<
    HTMLDivElement,
    HTMLDivElement
  >();

  const isEmpty = turns.length === 0;

  // A resumed conversation is a new scroll context even though the same DOM
  // viewport is reused. Without this, switching from a conversation that was
  // scrolled halfway up would open the next one at that arbitrary offset.
  useLayoutEffect(() => {
    scrollToBottom("auto");
  }, [conversationId, scrollToBottom]);

  function submit() {
    const value = question.trim();
    if (!value || !canAsk || isStreaming) return;
    setQuestion("");
    onAsk(value);
  }

  function insertDictation(transcript: string) {
    const textarea = questionRef.current;
    const start = textarea?.selectionStart ?? question.length;
    const end = textarea?.selectionEnd ?? start;
    const spliced = spliceTranscript(question, transcript, start, end);
    setQuestion(spliced.value);
    requestAnimationFrame(() => {
      questionRef.current?.focus();
      questionRef.current?.setSelectionRange(spliced.caret, spliced.caret);
    });
  }

  return (
    <section
      aria-label="Ask this video"
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      <p className="sr-only" role="status" aria-live="polite">
        {liveStatus(turns)}
      </p>

      <div
        ref={viewportRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
      >
        <div
          ref={contentRef}
          className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-5 sm:px-6"
        >
          {isEmpty ? (
            <VideoWelcome
              chapters={chapters}
              canAsk={canAsk}
              onAsk={onAsk}
            />
          ) : (
            turns.map((turn, index) => (
              <VideoTurnView
                key={turn.id}
                videoId={videoId}
                turn={turn}
                isLast={index === turns.length - 1}
                canRetry={canAsk && !isStreaming}
                onRetry={onRetry}
                onSeek={onSeek}
                onOpenDocument={onOpenDocument}
                onAskOnTheSide={onAskOnTheSide}
              />
            ))
          )}
        </div>
        {onAskOnTheSide && (
          <AskSelection container={contentRef} onAsk={onAskOnTheSide} />
        )}
      </div>

      <div className="relative border-t border-border bg-background">
        {/* Sits just above the composer's top edge with a fade behind it,
            rather than floating over live message content. */}
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

        <div className="mx-auto w-full max-w-3xl space-y-2 px-4 py-3 sm:px-6">
          {blockedReason ? (
            <p className="text-xs text-muted-foreground" role="status">
              {blockedReason}
            </p>
          ) : null}
          <div className="flex items-end gap-2">
            <Textarea
              ref={questionRef}
              id="question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              placeholder="Ask about this lecture…"
              aria-label="Ask about this lecture"
              disabled={!canAsk}
              rows={2}
              className="min-h-16 resize-none"
            />
            <MicButton
              size="icon"
              disabled={!canAsk}
              label="Dictate a question about this lecture"
              onTranscript={insertDictation}
            />
            {isStreaming ? (
              <Button variant="outline" onClick={onStop} aria-label="Stop">
                <Square aria-hidden />
              </Button>
            ) : (
              <Button
                onClick={submit}
                disabled={!canAsk || question.trim().length === 0}
                aria-label="Ask"
              >
                {isStreaming ? (
                  <Loader2 aria-hidden className="animate-spin" />
                ) : (
                  <Send aria-hidden />
                )}
              </Button>
            )}
          </div>
          <p className="text-center text-[0.7rem] text-muted-foreground">
            Answers are limited to this lecture&apos;s transcript, frames, and
            linked documents.
          </p>
        </div>
      </div>
    </section>
  );
}
