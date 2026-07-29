"use client";

import { ChevronDown, FlaskConical } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { TurnResult } from "@/lib/types";
import { cn } from "@/lib/utils";

const ROUTE_LABELS: Record<TurnResult["route"], string> = {
  hierarchy_summary: "Complete-scope summary",
  hierarchy_list: "Hierarchy listing",
  retrieval_qa: "Retrieval question answering",
  prior_answer_transform: "Transform of the previous answer",
  clarify: "Clarification",
};

const DEPENDENCY_LABELS: Record<TurnResult["history_dependency"], string> = {
  independent: "Answerable on its own",
  dependent: "Depends on earlier turns",
  ambiguous: "Ambiguous without history",
};

const ARCHETYPE_LABELS = {
  concept_explanation: "Concept explanation",
  system_design: "System-design walkthrough",
  chapter_review: "Interview revision",
  answer_transform: "Answer transformation",
} as const;

const DEPTH_LABELS = {
  quick: "Quick answer",
  interview: "Interview answer",
  deep: "Deep dive",
} as const;

function Row({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-3 border-b border-border py-1.5 last:border-b-0">
      <dt className="text-xs text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words text-xs">{children}</dd>
    </div>
  );
}

/**
 * Per-answer provenance.
 *
 * This is the "inspectable agentic workflow" made visible: which route the
 * graph took, what it actually searched for, what scope it resolved, and how
 * each piece of evidence ranked. It replaces a sidebar panel that only ever
 * described the most recent turn.
 */
export function AnswerInspector({ result }: { result: TurnResult }) {
  const [open, setOpen] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
        )}
      >
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
          {result.answer_archetype && (
            <Row term="Answer style">
              {ARCHETYPE_LABELS[result.answer_archetype]}
            </Row>
          )}
          {result.response_depth && (
            <Row term="Depth">{DEPTH_LABELS[result.response_depth]}</Row>
          )}
          {result.routing_reason && (
            <Row term="Routing reason">{result.routing_reason}</Row>
          )}
          {result.prompt_profile_version && (
            <Row term="Prompt version">
              <span className="font-mono">{result.prompt_profile_version}</span>
            </Row>
          )}
          {result.standalone_query && (
            <Row term="Searched for">
              <span className="font-mono">{result.standalone_query}</span>
            </Row>
          )}
          {result.retrieval_mode && (
            <Row term="Retrieval">{result.retrieval_mode}</Row>
          )}
          {result.resolved_scope && (
            <Row term="Scope">
              {result.resolved_scope.display_path}{" "}
              <span className="text-muted-foreground">
                (pp. {result.resolved_scope.start_page}–
                {result.resolved_scope.end_page})
              </span>
            </Row>
          )}
          <Row term="Outcome">
            <Badge
              variant={result.outcome === "answer" ? "secondary" : "outline"}
            >
              {result.outcome}
            </Badge>
          </Row>

          {result.evidence.length > 0 && (
            <Row term="Evidence">
              <ol className="space-y-0.5">
                {result.evidence.map((reference, index) => (
                  <li
                    key={`${reference.node_id}-${index}`}
                    className="flex gap-2 tabular-nums"
                  >
                    <span className="text-muted-foreground">{index + 1}.</span>
                    <span className="min-w-0 flex-1 truncate">
                      {reference.path}
                    </span>
                    {reference.score != null && (
                      <span className="text-muted-foreground">
                        {reference.score.toFixed(3)}
                      </span>
                    )}
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
