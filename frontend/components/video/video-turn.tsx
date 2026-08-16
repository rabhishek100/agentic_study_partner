"use client";

import {
  AlertCircle,
  Check,
  Copy,
  MessageSquarePlus,
  RotateCcw,
} from "lucide-react";
import { useEffect, useState } from "react";

import { DocumentPages } from "@/components/video/document-pages";
import { VisualEvidence } from "@/components/video/evidence-cards";
import { VideoAnswer, citedFrameIds } from "@/components/video/video-answer";
import { VideoInspector } from "@/components/video/video-inspector";
import { VideoReferences } from "@/components/video/video-references";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type {
  VideoDocumentTarget,
  VideoTurn as VideoTurnModel,
} from "@/lib/video-types";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <Button
      variant="ghost"
      size="xs"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
      }}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

/** Three dots that read as "working" without claiming a percentage. */
export function ThinkingIndicator({ label }: { label: string }) {
  return (
    <p
      className="flex items-center gap-1.5 text-sm text-muted-foreground"
      role="status"
    >
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="size-1.5 animate-bounce rounded-full bg-primary"
            style={{ animationDelay: `${index * 0.15}s` }}
          />
        ))}
      </span>
      {label}
    </p>
  );
}

export interface VideoTurnViewProps {
  videoId: string;
  turn: VideoTurnModel;
  isLast: boolean;
  canRetry: boolean;
  onRetry(): void;
  onSeek(milliseconds: number): void;
  onOpenDocument(target: VideoDocumentTarget): void;
  /**
   * Opens a side chat anchored to this answer. Offered only for a turn the
   * server recorded, since an anchor names a stored turn index.
   */
  onAskOnTheSide?: (turnIndex: number, quotedText: string) => void;
}

/**
 * One question and its answer, in the same shape the book chat uses.
 *
 * The reading path and the diagnostic path are deliberately separated: the
 * answer, its visual evidence, and the reference list are what the reader
 * consults, while scores, retrieval methods, and the trace live behind the
 * inspector. They used to share one "Evidence and diagnostics" disclosure,
 * which meant checking a source required opening a panel of ranking numbers.
 */
export function VideoTurnView({
  videoId,
  turn,
  isLast,
  canRetry,
  onRetry,
  onSeek,
  onOpenDocument,
  onAskOnTheSide,
}: VideoTurnViewProps) {
  const result = turn.result;
  const showThinking = turn.status === "streaming" && !turn.answer;

  return (
    <article
      className="space-y-4"
      aria-labelledby={`question-${turn.id}`}
      // Read by the selection popover to attribute a highlight to this turn.
      data-turn-index={turn.turnIndex ?? undefined}
    >
      <div className="flex justify-end">
        <h3
          id={`question-${turn.id}`}
          className="max-w-[85%] rounded-2xl rounded-br-sm bg-secondary px-4 py-2.5 text-xs font-normal text-secondary-foreground"
        >
          {turn.question}
        </h3>
      </div>

      <div className="space-y-3">
        {showThinking && <ThinkingIndicator label="Searching the lecture…" />}

        {turn.answer &&
          (result ? (
            <VideoAnswer
              videoId={videoId}
              answer={turn.answer}
              evidence={result.evidence}
              citations={result.citations}
              onSeek={onSeek}
              onOpenDocument={onOpenDocument}
            />
          ) : (
            // Mid-stream there are no citations to resolve yet, so the markers
            // would render as literal "[S1]" text. Plain prose until the turn
            // settles is the honest interim.
            <p className="whitespace-pre-wrap text-sm leading-relaxed">
              {turn.answer}
            </p>
          ))}

        {result && (
          // Only what the answer did not already show beside its own prose.
          // A frame illustrating a claim and repeated in a strip below is the
          // same picture twice; what is left here is visual evidence the
          // retrieval found and the answer never cited, which is worth
          // offering and worth keeping distinct from what it did.
          <VisualEvidence
            videoId={videoId}
            cards={result.visual_cards.filter(
              (card) =>
                !card.frame_id ||
                !citedFrameIds(result.citations).has(card.frame_id),
            )}
            onSeek={onSeek}
          />
        )}

        {result && (
          <DocumentPages
            videoId={videoId}
            evidence={result.evidence}
            citations={result.citations}
            onOpen={onOpenDocument}
          />
        )}

        {result && (
          <VideoReferences
            evidence={result.evidence}
            citations={result.citations}
            onSeek={onSeek}
            onOpenDocument={onOpenDocument}
          />
        )}

        {turn.status === "stopped" && (
          <p className="text-xs text-muted-foreground">
            Stopped before the answer finished. This turn was not added to the
            conversation, so a follow-up question will not see it.
          </p>
        )}

        {turn.status === "failed" && turn.error && (
          <Alert variant="destructive">
            <AlertCircle aria-hidden />
            <AlertDescription>{turn.error}</AlertDescription>
          </Alert>
        )}

        {turn.status !== "streaming" && (
          <div className="flex flex-wrap items-center gap-1">
            {turn.answer && <CopyButton text={turn.answer} />}
            {onAskOnTheSide && turn.turnIndex != null && turn.answer && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => onAskOnTheSide(turn.turnIndex!, turn.answer)}
              >
                <MessageSquarePlus aria-hidden />
                Ask on the side
              </Button>
            )}
            {isLast && canRetry && (
              <Button variant="ghost" size="xs" onClick={onRetry}>
                <RotateCcw aria-hidden />
                {turn.status === "failed" ? "Try again" : "Regenerate"}
              </Button>
            )}
            {result && <VideoInspector result={result} />}
          </div>
        )}
      </div>
    </article>
  );
}
