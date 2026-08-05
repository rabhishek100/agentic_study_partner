"use client";

import { AlertCircle, ArrowDown, Loader2, Send, Square } from "lucide-react";
import { useLayoutEffect, useState } from "react";

import { TurnDiagnostics, VisualEvidence } from "@/components/video/evidence-cards";
import { VideoAnswer } from "@/components/video/video-answer";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useScrollAnchor } from "@/hooks/use-scroll-anchor";
import type { VideoCitationRef, VideoTurn } from "@/lib/video-types";

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
  isStreaming: boolean;
  canAsk: boolean;
  blockedReason: string | null;
  /** Changes when a different conversation is opened, resetting the scroll. */
  conversationId: string | null;
  onAsk(question: string): void;
  onStop(): void;
  onSeek(milliseconds: number): void;
  onOpenDocument(citation: VideoCitationRef): void;
}

export function AskPane({
  videoId,
  turns,
  isStreaming,
  canAsk,
  blockedReason,
  conversationId,
  onAsk,
  onStop,
  onSeek,
  onOpenDocument,
}: AskPaneProps) {
  const [question, setQuestion] = useState("");
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
          className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-5 sm:px-6"
        >
          {isEmpty ? (
            <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
              Ask about anything in this lecture — what was said, what was
              drawn, or what a slide shows. Answers cite the moment they came
              from.
            </p>
          ) : null}
          {turns.map((turn) => (
            <article key={turn.id} className="space-y-2">
              <div className="flex justify-end">
                <h3 className="max-w-[85%] rounded-2xl rounded-br-sm bg-secondary px-4 py-2.5 text-[0.95rem] font-normal text-secondary-foreground">
                  {turn.question}
                </h3>
              </div>
              {turn.status === "failed" ? (
                <Alert variant="destructive">
                  <AlertCircle aria-hidden />
                  <AlertDescription>{turn.error}</AlertDescription>
                </Alert>
              ) : turn.result ? (
                <div className="space-y-3">
                  <VideoAnswer
                    answer={turn.answer}
                    evidence={turn.result.evidence}
                    citations={turn.result.citations}
                    onSeek={onSeek}
                    onOpenDocument={onOpenDocument}
                  />
                  <VisualEvidence
                    videoId={videoId}
                    cards={turn.result.visual_cards}
                    onSeek={onSeek}
                  />
                  <TurnDiagnostics result={turn.result} onSeek={onSeek} />
                </div>
              ) : (
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                  {turn.answer || "Searching the lecture…"}
                </p>
              )}
            </article>
          ))}
        </div>
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
        </div>
      </div>
    </section>
  );
}
