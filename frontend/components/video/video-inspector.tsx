"use client";

import { ChevronDown, FlaskConical } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  formatTimestamp,
  type VideoEvidenceRef,
  type VideoTurnResult,
} from "@/lib/video-types";
import { cn } from "@/lib/utils";

const ROUTE_LABELS: Record<VideoTurnResult["route"], string> = {
  evidence_qa: "Retrieval question answering",
  lecture_summary: "Complete-transcript summary",
  topic_inventory: "Topic inventory",
  prior_answer_transform: "Transform of the previous answer",
  clarify: "Clarification",
};

const DEPENDENCY_LABELS: Record<
  VideoTurnResult["history_dependency"],
  string
> = {
  independent: "Answerable on its own",
  dependent: "Depends on earlier turns",
  ambiguous: "Ambiguous without history",
};

function Row({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-3 border-b border-border py-1.5 last:border-b-0">
      <dt className="text-xs text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words text-xs">{children}</dd>
    </div>
  );
}

function rankLocator(reference: VideoEvidenceRef): string {
  return reference.modality === "resource_page"
    ? `${reference.resource_title ?? "Document"} p. ${reference.page_number ?? "?"}`
    : formatTimestamp(reference.start_ms);
}

/**
 * Per-answer provenance for one lecture turn.
 *
 * The same panel the book chat offers, over the decisions a video turn
 * actually makes: which route the graph took, what it rewrote the question
 * into, whether the first retrieval was judged sufficient or broadened, and
 * how each piece of evidence ranked in which modality.
 *
 * The evidence rows here carry scores and retrieval methods on purpose — this
 * is the diagnostic view. The reference list above it is the reading view and
 * deliberately carries neither.
 */
export function VideoInspector({ result }: { result: VideoTurnResult }) {
  const [open, setOpen] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
        <FlaskConical className="size-3.5" aria-hidden />
        How this answer was built
        <ChevronDown
          className={cn("size-3.5 transition-transform", open && "rotate-180")}
          aria-hidden
        />
      </CollapsibleTrigger>

      <CollapsibleContent>
        <dl className="mt-2 rounded-lg border border-border bg-muted/40 px-3 py-1">
          <Row term="Route">{ROUTE_LABELS[result.route] ?? result.route}</Row>
          <Row term="Question type">
            {DEPENDENCY_LABELS[result.history_dependency]}
          </Row>
          {result.routing_reason && (
            <Row term="Routing reason">{result.routing_reason}</Row>
          )}
          {result.standalone_query &&
            result.standalone_query !== result.question && (
              <Row term="Searched for">
                <span className="font-mono">{result.standalone_query}</span>
              </Row>
            )}
          {result.retrieval_attempts > 0 && (
            <Row term="Retrieval passes">
              {result.retrieval_attempts}
              {result.retrieval_attempts > 1 &&
                " — the first set was judged insufficient and the search was broadened"}
            </Row>
          )}
          {result.sufficiency_reason && (
            <Row
              term={
                result.retrieval_attempts === 0 ? "Evidence read" : "Sufficiency"
              }
            >
              {result.sufficiency_reason}
            </Row>
          )}
          <Row term="Outcome">
            <Badge
              variant={result.outcome === "answer" ? "secondary" : "outline"}
            >
              {result.outcome}
            </Badge>
          </Row>
          <Row term="Cost">${result.cost_usd.toFixed(4)}</Row>
          <Row term="Trace">
            {result.trace_id ? (
              <span className="font-mono">{result.trace_id.slice(0, 8)}</span>
            ) : (
              "not traced"
            )}
          </Row>

          {result.evidence.length > 0 && (
            <Row term="Evidence">
              <ol className="space-y-0.5">
                {result.evidence.map((reference) => (
                  <li
                    key={reference.evidence_id}
                    className="flex gap-2 tabular-nums"
                  >
                    <span className="text-muted-foreground">
                      {reference.rank}.
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      {rankLocator(reference)}
                      <span className="ml-1.5 text-muted-foreground">
                        {reference.retrieval_method}
                      </span>
                    </span>
                    <span className="text-muted-foreground">
                      {reference.score.toFixed(3)}
                    </span>
                  </li>
                ))}
              </ol>
            </Row>
          )}

          {result.warnings.length > 0 && (
            <Row term="Warnings">
              <ul className="space-y-1">
                {result.warnings.map((warning) => (
                  <li key={warning} className="text-destructive">
                    {warning}
                  </li>
                ))}
              </ul>
            </Row>
          )}
        </dl>
      </CollapsibleContent>
    </Collapsible>
  );
}
