"use client";

import { AlertCircle, Quote } from "lucide-react";

import { Answer } from "@/components/conversation/answer";
import { AnswerInspector } from "@/components/conversation/inspector";
import { References } from "@/components/conversation/references";
import { ThinkingIndicator } from "@/components/conversation/turn-view";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { anchorLabel, anchorText } from "@/lib/anchors";
import type { Anchor, ChatTurn, EvidenceRef } from "@/lib/types";

/**
 * The passages this side chat is anchored to.
 *
 * Shown at the top of the window and never scrolled away with the turns,
 * because it is the thing the whole thread is about — and because it is the
 * reader's own evidence that the window is asking about what they highlighted.
 */
export function AnchorChips({ anchors }: { anchors: Anchor[] }) {
  if (anchors.length === 0) return null;
  return (
    <ul className="shrink-0 space-y-2 border-b border-border bg-surface px-3 py-2">
      {anchors.map((anchor) => {
        const text = anchorText(anchor);
        return (
          <li key={anchor.anchor_id} className="flex gap-2">
            <Quote
              aria-hidden
              className="mt-[0.2em] size-[1em] shrink-0 text-muted-foreground"
            />
            {text ? (
              <blockquote className="side-chat-ui line-clamp-3 italic leading-snug text-muted-foreground">
                {text}
              </blockquote>
            ) : (
              // A page anchor carries no selection: the reader made none, they
              // were simply there. Naming the place is the whole chip.
              <span className="side-chat-ui leading-snug text-muted-foreground">
                {anchorLabel(anchor)}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export interface SideChatTurnsProps {
  turns: ChatTurn[];
  isLoading: boolean;
  /** Sent, but waiting for one of the shared generation slots. */
  isQueued?: boolean;
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
  isQueued = false,
  onOpenReference,
}: SideChatTurnsProps) {
  if (isLoading && turns.length === 0) {
    return (
      <p className="side-chat-ui px-3 py-4 text-muted-foreground" role="status">
        Loading this side chat…
      </p>
    );
  }

  if (turns.length === 0) {
    return (
      <p className="side-chat-ui px-3 py-4 leading-relaxed text-muted-foreground">
        Ask about the highlighted passage. Answers are grounded in the same
        books as the main conversation, so this can go beyond what the passage
        itself says.
      </p>
    );
  }

  return (
    <div className="space-y-6 px-3 py-3">
      {turns.map((turn) => (
        <article key={turn.id} className="space-y-3">
          {/* The reader's own question, mirroring the main conversation's
              right-aligned bubble at this window's scale. */}
          <p className="side-chat-ui ml-auto w-fit max-w-[92%] rounded-xl rounded-br-sm bg-secondary px-3 py-2 leading-snug text-secondary-foreground">
            {turn.question}
          </p>

          {turn.status === "streaming" &&
            !turn.answer &&
            // Says which of the two waits this is: a queued turn has not
            // reached the API yet, and looks stuck if it claims otherwise.
            (isQueued ? (
              <p className="side-chat-ui text-muted-foreground" role="status">
                Waiting for the other side chats to finish…
              </p>
            ) : (
              <ThinkingIndicator label="Checking the book…" />
            ))}

          {turn.answer && (
            <Answer
              text={turn.answer}
              evidence={turn.result?.evidence ?? []}
              citations={turn.result?.citations ?? []}
              figures={turn.result?.figures ?? []}
              onOpenReference={onOpenReference}
            />
          )}

          {turn.result && turn.result.evidence.length > 0 && (
            <References
              evidence={turn.result.evidence}
              citations={turn.result.citations}
              onOpenReference={onOpenReference}
            />
          )}

          {turn.status === "stopped" && (
            <p className="side-chat-ui text-muted-foreground">
              Stopped before the answer finished, so this turn was not recorded.
            </p>
          )}

          {turn.status === "failed" && turn.error && (
            <Alert variant="destructive">
              <AlertCircle aria-hidden />
              <AlertDescription className="side-chat-ui">
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
