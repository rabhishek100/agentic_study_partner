"use client";

import {
  AlertCircle,
  BookOpen,
  Check,
  Copy,
  MessageSquarePlus,
  RotateCcw,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Answer } from "@/components/conversation/answer";
import { Figures } from "@/components/conversation/figures";
import { AnswerInspector } from "@/components/conversation/inspector";
import { References } from "@/components/conversation/references";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { figuresForMarker, resolveMarker } from "@/lib/citations";
import type { ChatTurn, EvidenceRef } from "@/lib/types";

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
      className="flex items-center gap-2 text-sm text-muted-foreground"
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

export interface TurnViewProps {
  turn: ChatTurn;
  isLast: boolean;
  canRetry: boolean;
  onRetry: () => void;
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
  /**
   * Opens a side chat anchored to this answer. Offered only for a turn the
   * server recorded, since an anchor names a stored turn index.
   */
  onAskOnTheSide?: (turnIndex: number, quotedText: string) => void;
  /** Points the right region at this turn's sources. */
  onShowSources?: () => void;
  /** Whether the region is currently showing this turn. */
  sourcesShown?: boolean;
}

export function TurnView({
  turn,
  isLast,
  canRetry,
  onRetry,
  onOpenReference,
  onAskOnTheSide,
  onShowSources,
  sourcesShown,
}: TurnViewProps) {
  const showThinking = turn.status === "streaming" && !turn.answer;
  const result = turn.result;

  return (
    <article
      className="space-y-4"
      aria-labelledby={`question-${turn.id}`}
      // Lets a text selection be traced back to the turn it sits in, which is
      // what a side chat anchors to. Absent for a turn the server never
      // recorded, so a selection there offers nothing to anchor.
      data-turn-index={turn.turnIndex ?? undefined}
    >
      <div className="flex justify-end">
        <h3
          id={`question-${turn.id}`}
          className="max-w-[85%] rounded-2xl rounded-br-sm bg-secondary px-4 py-3 text-xs font-normal text-secondary-foreground"
        >
          {turn.question}
        </h3>
      </div>

      <div className="space-y-3">
        {showThinking && <ThinkingIndicator label="Checking the book…" />}

        {turn.answer && (
          <Answer
            text={turn.answer}
            evidence={result?.evidence ?? []}
            citations={result?.citations ?? []}
            figures={result?.figures ?? []}
            onOpenReference={onOpenReference}
          />
        )}

        {/* Anything no marker claimed still appears, after the prose. */}
        {result && (
          <Figures
            figures={result.figures.filter(
              (figure) =>
                !result.citations.some((citation) =>
                  figuresForMarker(
                    [figure],
                    resolveMarker(
                      citation.marker,
                      result.evidence,
                      result.citations,
                    ),
                  ).length,
                ),
            )}
          />
        )}

        {/*
          The sources themselves live in the right region, not under the answer.
          That is the whole composition: the page is asymmetric because grounding
          fills the space beside the prose. What stays here is the way back to
          them — which turn's evidence the region is showing is only meaningful
          per answer, so each answer can claim it.
        */}
        {result && (result.evidence.length > 0 || (result.web_sources && result.web_sources.length > 0)) && (
          onShowSources ? (
            <div>
              <Button
                variant="ghost"
                size="xs"
                aria-pressed={sourcesShown}
                onClick={onShowSources}
              >
                <BookOpen aria-hidden />
                {result.evidence.length > 0
                  ? `${result.evidence.length} ${result.evidence.length === 1 ? "source" : "sources"}`
                  : "Sources"}
                {sourcesShown ? (
                  <span className="text-muted-foreground">shown</span>
                ) : null}
              </Button>
            </div>
          ) : (
            <References
              evidence={result.evidence}
              citations={result.citations}
              webSources={result.web_sources}
              onOpenReference={onOpenReference}
            />
          )
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
            {result && <AnswerInspector result={result} />}
          </div>
        )}
      </div>
    </article>
  );
}
