"use client";

import { Check, CircleDashed, Globe, Layers, Lock } from "lucide-react";

import { cn } from "@/lib/utils";
import type { GroundingRung, WideningStep } from "@/lib/types";
import { isGrounded } from "@/lib/types";

const RUNG_LABELS: Record<GroundingRung, string> = {
  anchor: "This passage",
  open_source: "This source",
  library: "Your library",
  model_knowledge: "Outside your sources",
  web_search: "Outside your sources · the web",
};

/**
 * Where an answer came from, said at a glance.
 *
 * Escalation is automatic, which is what makes the label load-bearing rather
 * than decorative: a reader who is never asked cannot notice they left their
 * own material unless the answer says so. `anchor` and `open_source` are
 * distinguished because "here" and "somewhere in this book" are different
 * claims about the same source.
 */
export function RungBadge({
  rung,
  sourceType,
  className,
}: {
  rung?: GroundingRung | null;
  sourceType?: "book_library" | "model_knowledge" | "web_search";
  className?: string;
}) {
  if (!rung) return null;
  const grounded = isGrounded({ grounding_rung: rung, source_type: sourceType });
  const Icon =
    rung === "web_search"
      ? Globe
      : rung === "model_knowledge"
        ? CircleDashed
        : rung === "library"
          ? Layers
          : Check;

  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-2 rounded-full px-2 text-xs",
        grounded
          ? "bg-citation-muted text-citation"
          : // Neutral rather than a warning tone: leaving your sources is a
            // change of footing, not an error. The glyph carries the signal —
            // a solid check for grounded, an open dashed ring for not — so it
            // never rests on colour alone.
            "bg-accent text-foreground",
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {RUNG_LABELS[rung]}
    </span>
  );
}

/**
 * The notice above an answer that rests on nothing the reader owns.
 *
 * Structurally separate from the answer body, because the rule this feature
 * turns on is that grounded and ungrounded content never share one: an answer
 * either carries citations or carries this.
 */
export function OutOfSourceNotice({
  rung,
  widenings = [],
  onStayInSource,
  locked = false,
}: {
  rung?: GroundingRung | null;
  widenings?: WideningStep[];
  /** Offered so the reader can refuse the escalation for future questions. */
  onStayInSource?: () => void;
  locked?: boolean;
}) {
  if (!rung || isGrounded({ grounding_rung: rung })) return null;
  const why = widenings.at(-1)?.reason;

  return (
    <div className="mb-3 flex gap-3 rounded-lg border border-border bg-accent px-3 py-2">
      <CircleDashed
        aria-hidden
        className="mt-1 size-4 shrink-0 text-muted-foreground"
      />
      <div className="min-w-0 space-y-1">
        <p className="text-xs font-semibold">
          {rung === "web_search"
            ? "Outside your sources — from a web search"
            : "Outside your sources — general model knowledge"}
        </p>
        <p className="text-xs leading-snug text-muted-foreground">
          Nothing below is cited, and none of it was checked against anything
          you have ingested.
          {why ? ` The search stopped because: ${why}` : ""}
        </p>
        {onStayInSource && !locked && (
          <button
            type="button"
            onClick={onStayInSource}
            className="inline-flex items-center gap-2 rounded-md py-1 text-xs font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <Lock aria-hidden className="size-3.5" />
            Stay in this source from now on
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * The ladder a turn climbed, for the answer inspector.
 *
 * Every widening carries the verdict that provoked it, so "closest match" is
 * readable as a sequence of decisions rather than taken on trust.
 */
export function WideningTrail({ widenings }: { widenings: WideningStep[] }) {
  if (widenings.length === 0) return null;
  return (
    <ol className="space-y-1">
      {widenings.map((step, index) => (
        <li key={`${step.from_rung}-${step.to_rung}-${index}`}>
          <span className="font-mono text-xs">
            {step.from_rung} → {step.to_rung}
          </span>
          <span className="text-muted-foreground"> — {step.reason}</span>
        </li>
      ))}
    </ol>
  );
}
