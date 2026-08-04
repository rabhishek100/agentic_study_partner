"use client";

import { AlertCircle, Loader2, Send, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { TurnDiagnostics, VisualEvidence } from "@/components/video/evidence-cards";
import { VideoAnswer } from "@/components/video/video-answer";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { VideoCitationRef, VideoTurn } from "@/lib/video-types";

interface AskPaneProps {
  videoId: string;
  turns: VideoTurn[];
  isStreaming: boolean;
  canAsk: boolean;
  blockedReason: string | null;
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
  onAsk,
  onStop,
  onSeek,
  onOpenDocument,
}: AskPaneProps) {
  const [question, setQuestion] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns]);

  function submit() {
    const value = question.trim();
    if (!value || !canAsk || isStreaming) return;
    setQuestion("");
    onAsk(value);
  }

  return (
    <section
      aria-label="Ask this video"
      className="flex min-h-0 flex-1 flex-col"
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-1 py-2">
        {turns.length === 0 ? (
          <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
            Ask about anything in this lecture — what was said, what was drawn,
            or what a slide shows. Answers cite the moment they came from.
          </p>
        ) : null}
        {turns.map((turn) => (
          <article key={turn.id} className="space-y-2">
            <p className="rounded-lg bg-muted/60 px-3 py-2 text-sm font-medium">
              {turn.question}
            </p>
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
        <div ref={endRef} />
      </div>

      <div className="space-y-2 border-t border-border pt-3">
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
    </section>
  );
}
