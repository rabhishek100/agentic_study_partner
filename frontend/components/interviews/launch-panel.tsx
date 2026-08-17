"use client";

import {
  ArrowRight,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Panel,
  PanelContent,
  PanelHeader,
  PanelTitle,
} from "@/components/ui/panel";
import { SummaryRow } from "@/components/interviews/setup-controls";
import type { InterviewPreflight } from "@/lib/interview-types";
import { cn } from "@/lib/utils";

export type SetupOperation =
  | "idle"
  | "reviewing_source"
  | "creating_session"
  | "generating_question"
  | "opening_workspace";

const ACTIVITY: Record<
  Exclude<SetupOperation, "idle">,
  { title: string; detail: string }
> = {
  reviewing_source: {
    title: "Reviewing the selected source",
    detail:
      "Checking evidence readiness, detecting the interview format, and estimating topic coverage.",
  },
  creating_session: {
    title: "Creating your interview session",
    detail:
      "Saving the selected source, level, duration ceiling, and feedback mode.",
  },
  generating_question: {
    title: "Generating the first grounded question",
    detail:
      "Selecting the opening topic and validating its model answer against source evidence.",
  },
  opening_workspace: {
    title: "Opening the interview workspace",
    detail:
      "The first question is ready. Restoring voice narration and microphone capture.",
  },
};

type ReadinessState = "done" | "todo" | "blocked";

const READINESS_ICON: Record<ReadinessState, typeof CircleCheck> = {
  done: CircleCheck,
  todo: CircleDashed,
  blocked: CircleAlert,
};

/**
 * Never colour alone: the glyph differs per state, and the detail line says in
 * words what is outstanding. The glyph tint is the third signal, not the first.
 */
function ReadinessItem({
  state,
  label,
  detail,
  action,
}: {
  state: ReadinessState;
  label: string;
  detail: string;
  action?: React.ReactNode;
}) {
  const Icon = READINESS_ICON[state];
  return (
    <li className="flex items-start gap-3">
      <Icon
        aria-hidden
        className={cn(
          "mt-1 size-4 shrink-0",
          state === "done" && "text-positive",
          state === "todo" && "text-muted-foreground",
          state === "blocked" && "text-destructive",
        )}
      />
      <div className="min-w-0 grow">
        <p className="text-sm font-medium">
          {label}
          <span className="sr-only">
            {state === "done"
              ? " — ready"
              : state === "blocked"
                ? " — needs attention"
                : " — not done yet"}
          </span>
        </p>
        <p
          className={cn(
            "text-xs leading-5",
            state === "blocked" ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {detail}
        </p>
        {action ? <div className="pt-2">{action}</div> : null}
      </div>
    </li>
  );
}

export interface LaunchPanelProps {
  /** Plain-English restatement of the form, rendered above the checklist. */
  summary: {
    sourceTitle: string | null;
    sourceContext: string | null;
    level: string;
    duration: string;
    feedback: string;
    format: string;
    coding: string;
  };
  preflight: InterviewPreflight | null;
  /** The preflight was run against a setup the reader has since changed. */
  stale: boolean;
  microphoneReady: boolean;
  /**
   * The microphone controls, rendered inside the readiness line that requires
   * them. This was a numbered step of its own, which meant the panel could only
   * point at it — "grant access in step 3" — from a line the reader was already
   * looking at, about a step that had been pushed below the fold.
   */
  microphoneControl?: React.ReactNode;
  codingUnavailable: boolean;
  operation: SetupOperation;
  error: string;
  onCheck: () => void;
  onStart: () => void;
  onDropCodingExercise: () => void;
}

/**
 * The right column, doing a job.
 *
 * It used to hold a paragraph explaining what the preflight would do plus two
 * feature tiles, and it stayed inert until the reader pressed a button whose
 * label silently changed meaning. Three defects came out of that: the two-phase
 * commit was invisible, a settings change silently discarded the preflight and
 * reverted the button, and the microphone requirement only appeared *after* the
 * check — as a disabled button in one column explained by text in the other.
 *
 * A readiness checklist fixes all three with one structure. Every precondition
 * is visible from the first render, each says what is outstanding, and "changed
 * since the check" is a state with a name instead of a silent reset.
 */
export function LaunchPanel({
  summary,
  preflight,
  stale,
  microphoneReady,
  microphoneControl,
  codingUnavailable,
  operation,
  error,
  onCheck,
  onStart,
  onDropCodingExercise,
}: LaunchPanelProps) {
  const busy = operation !== "idle";
  const sourceChosen = Boolean(summary.sourceTitle);
  const checked = Boolean(preflight) && !stale;
  const blocked = checked && codingUnavailable;
  const canStart = checked && microphoneReady && !blocked;

  const checkDetail = !sourceChosen
    ? "Choose a source first."
    : stale
      ? "Your setup changed. Re-check to see the updated estimate."
      : preflight
        ? `${preflight.required_topic_count} topics · about ${preflight.estimated_min_minutes}–${preflight.estimated_max_minutes} min`
        : "Confirms the source has enough evidence before anything is generated.";

  return (
    /*
      `min-w-0`: the panel sits in a fixed 20rem grid track, and a grid item's
      default `min-width: auto` lets its content push past the track it was
      given. The panel then clipped its own summary values rather than the
      track holding them.
    */
    <Panel className="min-w-0 lg:sticky lg:top-6">
      <PanelHeader>
        <PanelTitle>Your session</PanelTitle>
      </PanelHeader>

      <PanelContent className="grid gap-4">
        <div className="grid gap-1">
          {summary.sourceTitle ? (
            <>
              <p className="font-serif text-base leading-snug font-medium">
                {summary.sourceTitle}
              </p>
              {summary.sourceContext ? (
                <p className="text-xs text-muted-foreground">{summary.sourceContext}</p>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              No source chosen yet.
            </p>
          )}
        </div>

        {/*
          One `Format` row, not two. A separate "Source check" block repeated
          the estimate and the topic count that the checklist line below already
          gives, and set the chosen format ("Detect from the source") beside the
          resolved one ("Concept") as though they were different facts. Once the
          check has run, the resolved format simply replaces the setting.
        */}
        <dl className="grid gap-2 border-t border-divider pt-4">
          <SummaryRow label="Level" value={summary.level} />
          <SummaryRow label="Time" value={summary.duration} />
          <SummaryRow label="Feedback" value={summary.feedback} />
          <SummaryRow
            label="Format"
            value={
              checked && preflight ? (
                <span className="capitalize">
                  {preflight.selected_format.replace("_", " ")}
                </span>
              ) : (
                summary.format
              )
            }
          />
          <SummaryRow label="Coding" value={summary.coding} />
        </dl>

        <ul className="grid gap-3 border-t border-divider pt-4">
          <ReadinessItem
            state={sourceChosen ? "done" : "todo"}
            label="Source selected"
            detail={
              sourceChosen
                ? "One chapter or one lecture, as the interview requires."
                : "Pick a book chapter or a processed lecture in step 1."
            }
          />
          <ReadinessItem
            state={blocked ? "blocked" : checked ? "done" : "todo"}
            label="Source checked"
            detail={
              blocked
                ? "This source has no executable material for a grounded coding exercise."
                : checkDetail
            }
            action={
              blocked ? (
                <Button size="sm" variant="outline" onClick={onDropCodingExercise}>
                  Continue without the coding exercise
                </Button>
              ) : null
            }
          />
          <ReadinessItem
            state={microphoneReady ? "done" : "todo"}
            label="Microphone ready"
            detail={
              microphoneReady
                ? "Access granted. Raw audio is never stored, and the live input test stays optional."
                : "Required — the interview is spoken. Raw audio is never stored."
            }
            action={microphoneControl}
          />
        </ul>

        <Button
          className="w-full"
          size="lg"
          disabled={!sourceChosen || busy || (checked && !canStart)}
          onClick={() => void (checked ? onStart() : onCheck())}
        >
          {busy ? (
            <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" />
          ) : stale ? (
            <RefreshCw aria-hidden />
          ) : null}
          {operation === "reviewing_source"
            ? "Reviewing source…"
            : operation === "creating_session"
              ? "Creating session…"
              : operation === "generating_question"
                ? "Generating first question…"
                : operation === "opening_workspace"
                  ? "Opening interview…"
                  : checked
                    ? microphoneReady
                      ? "Start interview"
                      : "Enable your microphone to start"
                    : stale
                      ? "Re-check the source"
                      : "Check this source"}
          {!busy && !stale ? <ArrowRight aria-hidden /> : null}
        </Button>

        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {operation !== "idle" ? (
          <div
            className="flex items-start gap-3 rounded-md border border-divider bg-surface-hover p-3"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <Loader2
              aria-hidden
              className="mt-1 size-4 shrink-0 animate-spin text-action motion-reduce:animate-none"
            />
            <div>
              <p className="text-sm font-medium">{ACTIVITY[operation].title}</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {ACTIVITY[operation].detail}
              </p>
            </div>
          </div>
        ) : null}

        {checked && preflight ? (
          <div className="grid gap-3 border-t border-divider pt-4">
            <p className="text-xs leading-5 text-muted-foreground">
              Coverage is what ends the session, not the clock. Exact questions
              stay hidden.
            </p>
            {preflight.warnings.map((warning) => (
              <Alert key={warning}>
                <AlertDescription>{warning}</AlertDescription>
              </Alert>
            ))}
          </div>
        ) : null}
      </PanelContent>
    </Panel>
  );
}
