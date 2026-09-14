"use client";

import { ArrowRight, BookOpen, Headphones, Loader2, Mic, RotateCw } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Panel, PanelContent } from "@/components/ui/panel";
import { SummaryRow } from "@/components/interviews/setup-controls";
import type { InterviewPreflight } from "@/lib/interview-types";
import { cn } from "@/lib/utils";

export type SetupOperation =
  | "idle"
  | "creating_session"
  | "generating_question"
  | "generating_ideal_flow"
  | "opening_workspace";

const ACTIVITY: Record<Exclude<SetupOperation, "idle">, string> = {
  creating_session: "Creating your session…",
  generating_question: "Writing the first question…",
  generating_ideal_flow: "Writing the complete interview…",
  opening_workspace: "Opening the interview…",
};

/**
 * A band of the panel. One rule above, one consistent inset below it.
 *
 * The panel previously mixed a container `gap-4` with per-section `pt-4`, so
 * every rule had 16px above it and 32px below — a rhythm that read as an
 * accident, because it was one.
 */
function Band({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-3 border-t border-divider pt-4 first:border-t-0 first:pt-0",
        className,
      )}
      {...props}
    />
  );
}

/** Uppercase, 12px, and never the only thing carrying a meaning. */
function BandLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground">
      {children}
    </p>
  );
}

export interface LaunchPanelProps {
  /** Plain-English restatement of the form. */
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
  /** The source inspection is in flight, or waiting out its debounce. */
  checking: boolean;
  /** The inspection failed; the reader can ask for it again. */
  checkError: string;
  microphoneReady: boolean;
  /** The microphone controls, rendered in the section that requires them. */
  microphoneControl?: React.ReactNode;
  codingUnavailable: boolean;
  operation: SetupOperation;
  error: string;
  onRetryCheck: () => void;
  onStart: () => void;
  onListen?: () => void;
  listenAvailable?: boolean;
  onDropCodingExercise: () => void;
}

/**
 * What you are about to start, and the one thing left to do before you can.
 *
 * Two rounds of this panel are folded in. The first replaced an inert
 * explanation with a readiness checklist, because the screen used to hide the
 * preconditions for starting until the reader had already pressed a button.
 * This one removes a precondition rather than reporting it: the source check is
 * deterministic and free of model calls, so making the reader ask for it was a
 * step we invented. It now runs on its own and simply reports, which leaves one
 * button on the screen instead of one button with two meanings.
 */
export function LaunchPanel({
  summary,
  preflight,
  checking,
  checkError,
  microphoneReady,
  microphoneControl,
  codingUnavailable,
  operation,
  error,
  onRetryCheck,
  onStart,
  onListen,
  listenAvailable = false,
  onDropCodingExercise,
}: LaunchPanelProps) {
  const busy = operation !== "idle";
  const activityText = operation === "idle" ? "" : ACTIVITY[operation];
  const startingLiveInterview = [
    "creating_session",
    "generating_question",
    "opening_workspace",
  ].includes(operation);
  const sourceChosen = Boolean(summary.sourceTitle);
  const checked = Boolean(preflight) && !checking && !checkError;
  const canStart = checked && microphoneReady && !codingUnavailable && !busy;

  /** Why the button is not available. Stated under it, not crammed into it. */
  const blockedBecause = !sourceChosen
    ? "Choose a book chapter or a lecture in step 1."
    : checkError
      ? "The source could not be inspected."
      : checking
        ? null
        : codingUnavailable
          ? "This source cannot ground the coding exercise you asked for."
          : !microphoneReady
            ? "Turn on your microphone above — the interview is spoken."
            : null;

  return (
    /*
      Two separate things keep this panel inside its 20rem track, and it needs
      both.

      `min-w-0` on the panel: as a grid item its default `min-width: auto`
      would let content push it past the track it was given.

      `grid-cols-1` on every grid below: a bare `grid` has no explicit column,
      so the implicit one is sized `auto`, and an `auto` track grows to
      max-content and overflows a container it cannot fit in. Nothing inside
      can shrink it, because the track — not the item — is what is too wide.
      Tailwind's `grid-cols-1` is `repeat(1, minmax(0, 1fr))`, which clamps it.

      Without the second, the panel's own `overflow-hidden` cut every summary
      value mid-word ("Mid-", "Rea", "Not inclu") whenever a long book title
      raised the max-content width. `min-w-0` alone does not fix it.
    */
    <Panel className="min-w-0 lg:sticky lg:top-6">
      <PanelContent className="grid grid-cols-1 gap-4">
        <Band>
          <BandLabel>Your session</BandLabel>
          {sourceChosen ? (
            <div className="grid grid-cols-1 gap-1">
              <p className="font-serif text-base leading-snug font-medium">
                {summary.sourceTitle}
              </p>
              {summary.sourceContext ? (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <BookOpen aria-hidden className="size-4 shrink-0" />
                  <span className="min-w-0 truncate">{summary.sourceContext}</span>
                </p>
              ) : null}
            </div>
          ) : (
            <p className="text-sm leading-6 text-muted-foreground">
              Choose a chapter or a lecture and this fills in with what the
              interview will cover.
            </p>
          )}

          {/*
            The inspection reports itself rather than waiting to be asked. All
            three states occupy the same line so the panel does not reflow
            underneath the reader while they are still choosing.
          */}
          {sourceChosen ? (
            <div
              role="status"
              aria-live="polite"
              className="min-h-9 rounded-md bg-surface-hover px-3 py-2 text-xs leading-5"
            >
              {checkError ? (
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-destructive">
                  Could not inspect this source.
                  <Button size="xs" variant="outline" onClick={onRetryCheck}>
                    <RotateCw aria-hidden />
                    Try again
                  </Button>
                </span>
              ) : checking || !preflight ? (
                <span className="flex items-center gap-2 text-muted-foreground">
                  <Loader2
                    aria-hidden
                    className="size-4 shrink-0 animate-spin motion-reduce:animate-none"
                  />
                  Checking the evidence in this source…
                </span>
              ) : (
                <span className="text-foreground">
                  <span className="capitalize">
                    {preflight.selected_format.replace("_", " ")}
                  </span>
                  {" · "}
                  {preflight.required_topic_count} topics{" · "}
                  <span className="whitespace-nowrap">
                    about {preflight.estimated_min_minutes}–
                    {preflight.estimated_max_minutes} min
                  </span>
                </span>
              )}
            </div>
          ) : null}

          {checked && preflight
            ? preflight.warnings.map((warning) => (
                <Alert key={warning}>
                  <AlertDescription>{warning}</AlertDescription>
                </Alert>
              ))
            : null}
        </Band>

        <Band>
          <dl className="grid grid-cols-1 gap-2">
            <SummaryRow label="Level" value={summary.level} />
            <SummaryRow label="Time" value={summary.duration} />
            <SummaryRow label="Feedback" value={summary.feedback} />
            <SummaryRow label="Coding" value={summary.coding} />
          </dl>
        </Band>

        <Band>
          <div className="flex items-center justify-between gap-2">
            <BandLabel>Microphone</BandLabel>
            {/* Not colour alone: the word is the status, the tint reinforces it. */}
            <span
              className={cn(
                "text-eyebrow font-semibold uppercase tracking-[0.1em]",
                microphoneReady ? "text-positive" : "text-muted-foreground",
              )}
            >
              {microphoneReady ? "Ready" : "Required"}
            </span>
          </div>
          {microphoneControl}
        </Band>

        <Band>
          {codingUnavailable ? (
            <Alert variant="destructive">
              <AlertDescription className="grid grid-cols-1 gap-2">
                <span>
                  This source has no executable material for a grounded coding
                  exercise.
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="justify-self-start"
                  onClick={onDropCodingExercise}
                >
                  Continue without it
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}

          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <Button className="w-full" size="lg" disabled={!canStart} onClick={onStart}>
            {startingLiveInterview ? (
              <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" />
            ) : (
              <Mic aria-hidden />
            )}
            {startingLiveInterview ? activityText : "Start interview"}
            {!startingLiveInterview ? <ArrowRight aria-hidden /> : null}
          </Button>

          {onListen ? (
            <Button
              className="w-full"
              size="lg"
              variant="outline"
              disabled={!checked || !listenAvailable || busy}
              onClick={onListen}
            >
              {operation === "generating_ideal_flow" ? (
                <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Headphones aria-hidden />
              )}
              {operation === "generating_ideal_flow"
                ? ACTIVITY.generating_ideal_flow
                : "Listen to ideal interview"}
            </Button>
          ) : null}

          {onListen && sourceChosen && !listenAvailable ? (
            <p className="text-center text-xs leading-5 text-muted-foreground">
              Ideal interview flows currently require a book chapter.
            </p>
          ) : null}

          {/*
            The reason sits under the button rather than replacing its label.
            A disabled control that renames itself to its own blocker reads as a
            different action, and the reader loses track of what they came for.
          */}
          {!busy && blockedBecause ? (
            <p className="text-center text-xs leading-5 text-muted-foreground">
              {blockedBecause}
            </p>
          ) : null}
          {busy ? (
            <p
              className="text-center text-xs leading-5 text-muted-foreground"
              role="status"
              aria-live="polite"
            >
              Keep this tab open. {operation === "generating_ideal_flow"
                ? "The complete grounded dialogue is being written from the chapter."
                : "Your first question is being written from the source."}
            </p>
          ) : null}
        </Band>
      </PanelContent>
    </Panel>
  );
}
