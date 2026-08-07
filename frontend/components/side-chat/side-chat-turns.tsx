"use client";

import { AlertCircle, Quote } from "lucide-react";

import { Answer } from "@/components/conversation/answer";
import { AnswerInspector } from "@/components/conversation/inspector";
import { References } from "@/components/conversation/references";
import { ThinkingIndicator } from "@/components/conversation/turn-view";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { ChatTurn, EvidenceRef, QuoteAnchor } from "@/lib/types";

/**
 * The passages this side chat is anchored to.
 *
 * Shown at the top of the window and never scrolled away with the turns,
 * because it is the thing the whole thread is about — and because it is the
 * reader's own evidence that the window is asking about what they highlighted.
 */
export function AnchorChips({ anchors }: { anchors: QuoteAnchor[] }) {
  if (anchors.length === 0) return null;
  return (
    <ul className="shrink-0 space-y-1 border-b border-border bg-muted/30 px-3 py-2">
      {anchors.map((anchor) => (
        <li key={anchor.anchor_id} className="flex gap-1.5">
          <Quote
            aria-hidden
            className="mt-0.5 size-3 shrink-0 text-muted-foreground"
          />
          <blockquote className="line-clamp-3 text-xs italic leading-snug text-muted-foreground">
            {anchor.quoted_text}
          </blockquote>
        </li>
      ))}
    </ul>
  );
}

export interface SideChatTurnsProps {
  turns: ChatTurn[];
  isLoading: boolean;
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
}

/**
 * A side chat's exchanges, rendered narrow.
 *
 * The answer body, citation chips, and reference cards are the same components
 * the main conversation uses, so a side answer is a first-class grounded answer
 * — its citations open the same reading pane at the same page. What is left out
 * is what a 384px window has no room for: the figure gallery, retry, and the
 * per-turn heading treatment.
 */
export function SideChatTurns({
  turns,
  isLoading,
  onOpenReference,
}: SideChatTurnsProps) {
  if (isLoading && turns.length === 0) {
    return (
      <p className="px-3 py-4 text-xs text-muted-foreground" role="status">
        Loading this side chat…
      </p>
    );
  }

  if (turns.length === 0) {
    return (
      <p className="px-3 py-4 text-xs text-muted-foreground">
        Ask about the highlighted passage. Answers are grounded in the same
        books as the main conversation, so this can go beyond what the passage
        itself says.
      </p>
    );
  }

  return (
    <div className="space-y-4 px-3 py-3">
      {turns.map((turn) => (
        <article key={turn.id} className="space-y-2">
          <p className="rounded-lg rounded-br-sm bg-secondary px-2.5 py-1.5 text-xs text-secondary-foreground">
            {turn.question}
          </p>

          {turn.status === "streaming" && !turn.answer && (
            <ThinkingIndicator label="Checking the book…" />
          )}

          {turn.answer && (
            <div className="text-[0.8rem]">
              <Answer
                text={turn.answer}
                evidence={turn.result?.evidence ?? []}
                citations={turn.result?.citations ?? []}
                figures={turn.result?.figures ?? []}
                onOpenReference={onOpenReference}
              />
            </div>
          )}

          {turn.result && turn.result.evidence.length > 0 && (
            <References
              evidence={turn.result.evidence}
              citations={turn.result.citations}
              onOpenReference={onOpenReference}
            />
          )}

          {turn.status === "stopped" && (
            <p className="text-[0.7rem] text-muted-foreground">
              Stopped before the answer finished, so this turn was not recorded.
            </p>
          )}

          {turn.status === "failed" && turn.error && (
            <Alert variant="destructive">
              <AlertCircle aria-hidden />
              <AlertDescription className="text-xs">
                {turn.error}
              </AlertDescription>
            </Alert>
          )}

          {turn.result && <AnswerInspector result={turn.result} />}
        </article>
      ))}
    </div>
  );
}
