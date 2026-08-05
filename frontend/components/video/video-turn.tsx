"use client";

import { AlertCircle, Check, Copy, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { VisualEvidence } from "@/components/video/evidence-cards";
import { VideoAnswer } from "@/components/video/video-answer";
import { VideoInspector } from "@/components/video/video-inspector";
import { VideoReferences } from "@/components/video/video-references";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type {
  VideoCitationRef,
  VideoEvidenceRef,
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
  onOpenDocument(citation: VideoCitationRef): void;
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
}: VideoTurnViewProps) {
  const result = turn.result;
  const showThinking = turn.status === "streaming" && !turn.answer;

  // A reference row carries the evidence itself; the document opener speaks in
  // citations, so the row is translated into the citation-shaped locator it
  // would have had if the answer had cited it.
  const openDocumentFor = (reference: VideoEvidenceRef) =>
    onOpenDocument({
      marker: `[S${reference.rank}]`,
      evidence_rank: reference.rank,
      modality: reference.modality,
      start_ms: reference.start_ms,
      page_number: reference.page_number,
      frame_id: reference.frame_id,
      resource_id: reference.resource_id,
    });

  return (
    <article className="space-y-4" aria-labelledby={`question-${turn.id}`}>
      <div className="flex justify-end">
        <h3
          id={`question-${turn.id}`}
          className="max-w-[85%] rounded-2xl rounded-br-sm bg-secondary px-4 py-2.5 text-[0.95rem] font-normal text-secondary-foreground"
        >
          {turn.question}
        </h3>
      </div>

      <div className="space-y-3">
        {showThinking && <ThinkingIndicator label="Searching the lecture…" />}

        {turn.answer &&
          (result ? (
            <VideoAnswer
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
          <VisualEvidence
            videoId={videoId}
            cards={result.visual_cards}
            onSeek={onSeek}
          />
        )}

        {result && (
          <VideoReferences
            evidence={result.evidence}
            citations={result.citations}
            onSeek={onSeek}
            onOpenDocument={openDocumentFor}
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
