"use client";

import {
  Check,
  Circle,
  Clock3,
  GraduationCap,
  Loader2,
  TriangleAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import type { CourseDetail } from "@/lib/course-types";
import {
  etaWindow,
  INGESTION_PHASES,
  isActiveVideoJob,
  phaseState,
  progressPercent,
  technicalDuration,
} from "@/lib/ingestion-progress";

export function CourseIngestionProgress({ course }: { course: CourseDetail }) {
  const active = course.lectures.filter((lecture) =>
    isActiveVideoJob(lecture.latest_ingestion),
  );
  const failed = course.lectures.filter(
    (lecture) => lecture.latest_ingestion?.status === "failed",
  );
  if (active.length === 0 && failed.length === 0) return null;

  const available = course.lectures.filter((lecture) => lecture.ready_for_qa).length;
  const estimates = active.map(
    (lecture) => lecture.latest_ingestion?.timing?.estimated_remaining_seconds,
  );
  const remaining = estimates.some((value) => value == null)
    ? null
    : estimates.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  const percent = course.lectures.length
    ? course.lectures.reduce(
        (sum, lecture) => sum + progressPercent(lecture.latest_ingestion),
        0,
      ) / course.lectures.length
    : 0;

  const stageCounts = new Map<string, number>();
  for (const lecture of active) {
    const stage = lecture.latest_ingestion?.stage ?? "queued";
    stageCounts.set(stage, (stageCounts.get(stage) ?? 0) + 1);
  }

  return (
    <section
      aria-labelledby="course-ingestion-title"
      aria-live="polite"
      className="shrink-0 border-b border-divider bg-surface px-4 py-3 sm:px-6"
    >
      <div className="mx-auto flex w-full max-w-6xl items-center gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-wash text-action">
          <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h2 id="course-ingestion-title" className="text-sm font-semibold">
              Improving course search
            </h2>
            <p className="text-xs text-muted-foreground">
              {available} lecture{available === 1 ? " is" : "s are"} available now
            </p>
          </div>
          <Progress
            value={percent}
            aria-label="Course upgrade progress"
            className="mt-2 h-1"
          />
        </div>

        <div className="hidden shrink-0 text-right sm:block">
          <p className="text-xs font-medium text-foreground">{etaWindow(remaining)}</p>
          <p className="text-xs text-muted-foreground">Runs in the background</p>
        </div>

        <Sheet>
          <SheetTrigger asChild>
            <Button variant="outline" size="sm" className="shrink-0">
              View progress
            </Button>
          </SheetTrigger>
          <SheetContent className="w-full overflow-y-auto bg-surface p-0 sm:max-w-md">
            <SheetHeader className="border-b border-divider p-6 pr-14">
              <SheetTitle>Course upgrade</SheetTitle>
              <SheetDescription>
                Everything stays available while we make answers more complete and easier to verify.
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-7 p-6">
              <section aria-labelledby="upgrade-status-title" className="space-y-3">
                <div className="flex items-start gap-3">
                  <span className="grid size-9 shrink-0 place-items-center rounded-full bg-wash text-action">
                    <GraduationCap aria-hidden className="size-4" />
                  </span>
                  <div>
                    <h3 id="upgrade-status-title" className="font-medium">
                      {available} of {course.lecture_count} lectures are ready to study
                    </h3>
                    <p className="mt-1 text-sm leading-5 text-muted-foreground">
                      Ask across the course now. The upgrade adds stronger search across speech,
                      slides, diagrams, and cited timestamps.
                    </p>
                  </div>
                </div>
                <Progress value={percent} aria-label="Detailed course upgrade progress" />
                <div className="flex items-center justify-between gap-4 text-xs">
                  <span className="text-muted-foreground">{Math.round(percent)}% complete</span>
                  <span className="flex items-center gap-2 font-medium text-foreground">
                    <Clock3 aria-hidden className="size-3.5" />
                    {etaWindow(remaining)}
                  </span>
                </div>
              </section>

              <section aria-labelledby="remaining-steps-title">
                <h3 id="remaining-steps-title" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  What is happening
                </h3>
                <ol className="mt-4 space-y-4">
                  {INGESTION_PHASES.map((phase, phaseIndex) => {
                    const states = course.lectures.map((lecture) =>
                      phaseState(lecture.latest_ingestion, phaseIndex),
                    );
                    const done = states.filter((state) => state === "done").length;
                    const working = states.filter((state) => state === "active").length;
                    const state =
                      done === course.lecture_count
                        ? "done"
                        : working > 0
                          ? "active"
                          : "pending";
                    const Icon = state === "done" ? Check : state === "active" ? Loader2 : Circle;
                    return (
                      <li key={phase.id} className="flex items-start gap-3">
                        <span className="mt-1 grid size-6 shrink-0 place-items-center">
                          <Icon
                            aria-hidden
                            className={`size-4 ${
                              state === "done"
                                ? "text-positive"
                                : state === "active"
                                  ? "animate-spin text-action motion-reduce:animate-none"
                                  : "text-disabled-foreground"
                            }`}
                          />
                        </span>
                        <div>
                          <p className={state === "active" ? "text-sm font-semibold" : "text-sm font-medium"}>
                            {phase.label}
                          </p>
                          <p className="mt-1 text-xs leading-5 text-muted-foreground">
                            {state === "done"
                              ? `Complete for all ${course.lecture_count} lectures`
                              : state === "active"
                                ? `${working} lecture${working === 1 ? " is" : "s are"} at this step`
                                : "Starts automatically after the earlier steps"}
                          </p>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>

              {failed.length ? (
                <p className="flex items-start gap-2 rounded-lg bg-destructive-wash p-3 text-xs text-destructive">
                  <TriangleAlert aria-hidden className="mt-1 size-3.5 shrink-0" />
                  {failed.length} lecture{failed.length === 1 ? " needs" : "s need"} attention.
                  Completed work is saved for a safe retry.
                </p>
              ) : null}

              <p className="text-xs leading-5 text-muted-foreground">
                You can leave this page. Processing and recovery continue automatically.
              </p>

              <details className="border-t border-divider pt-4 text-xs">
                <summary className="cursor-pointer font-medium text-muted-foreground hover:text-foreground">
                  Technical details
                </summary>
                <div className="mt-4 space-y-4">
                  <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
                    <dt className="text-muted-foreground">Worker time left</dt>
                    <dd>{technicalDuration(remaining)}</dd>
                    <dt className="text-muted-foreground">Provider spend</dt>
                    <dd>
                      ${course.actual_ingestion_cost_usd.toFixed(2)} of ${course.ingestion_cost_cap_usd.toFixed(2)} cap
                    </dd>
                    <dt className="text-muted-foreground">Recovery</dt>
                    <dd>Every lecture and stage is checkpointed; retries reuse completed work.</dd>
                  </dl>
                  <div>
                    <p className="font-medium">Current internal stages</p>
                    <ul className="mt-2 space-y-1 font-mono text-muted-foreground">
                      {[...stageCounts.entries()].map(([stage, count]) => (
                        <li key={stage}>{stage}: {count}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </details>
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </section>
  );
}
