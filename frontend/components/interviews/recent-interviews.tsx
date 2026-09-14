"use client";

import { CircleCheck, CirclePause, CircleSlash, Headphones, Play } from "lucide-react";
import Link from "next/link";

import { formatDuration, type IdealInterviewFlow, type InterviewSession } from "@/lib/interview-types";
import { cn } from "@/lib/utils";

const LEVEL_LABEL: Record<InterviewSession["target_level"], string> = {
  entry: "Entry",
  mid: "Mid-level",
  senior: "Senior",
};

/**
 * Status carries a glyph and a word, never a tone alone, and the two live
 * statuses say what the link *does* rather than what the row *is* — "Paused"
 * and "In progress" both mean "this one resumes", which the old outline badge
 * left the reader to infer.
 */
const STATUS: Record<
  InterviewSession["status"],
  { label: string; action: string; icon: typeof Play; tone: string }
> = {
  ready: { label: "Not started", action: "Open", icon: Play, tone: "text-muted-foreground" },
  active: { label: "In progress", action: "Resume", icon: Play, tone: "text-action" },
  paused: { label: "Paused", action: "Resume", icon: CirclePause, tone: "text-action" },
  completed: { label: "Complete", action: "Report", icon: CircleCheck, tone: "text-positive" },
  abandoned: { label: "Ended", action: "Report", icon: CircleSlash, tone: "text-muted-foreground" },
};

/** Coarse on purpose: the exact minute of a practice session is never the point. */
function relativeDay(timestamp: string | null): string | null {
  if (!timestamp) return null;
  const then = new Date(timestamp);
  if (Number.isNaN(then.getTime())) return null;
  const days = Math.floor((Date.now() - then.getTime()) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 30) return `${Math.floor(days / 7)} weeks ago`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function RecentInterviews({ sessions, idealFlows = [] }: { sessions: InterviewSession[]; idealFlows?: IdealInterviewFlow[] }) {
  return (
    <div className="grid gap-4">
      <div className="grid gap-1">
        <h2 className="text-sm font-medium">Recent interviews</h2>
        <p className="text-xs leading-5 text-muted-foreground">
          Resume a paused session or revisit a report.
        </p>
      </div>

      {sessions.length === 0 ? (
        <p className="rounded-md border border-dashed border-divider p-3 text-xs leading-5 text-muted-foreground">
          Your sessions will appear here once you finish setting one up.
        </p>
      ) : (
        <ul className="grid gap-1">
          {sessions.map((item) => {
            const status = STATUS[item.status];
            const Icon = status.icon;
            const when = relativeDay(item.started_at ?? item.created_at);
            return (
              <li key={item.session_id}>
                <Link
                  href={`/interviews/${item.session_id}`}
                  className="grid gap-1 rounded-md border border-transparent px-3 py-3 transition-colors hover:border-divider hover:bg-surface-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="line-clamp-2 font-serif text-sm font-medium">
                      {item.title.replace("Interview · ", "")}
                    </p>
                    <span className="shrink-0 text-xs font-medium text-muted-foreground">
                      {status.action}
                    </span>
                  </div>
                  <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                    <span className={cn("inline-flex items-center gap-1", status.tone)}>
                      <Icon aria-hidden className="size-4" />
                      {status.label}
                    </span>
                    <span aria-hidden>·</span>
                    <span>{LEVEL_LABEL[item.target_level]}</span>
                    <span aria-hidden>·</span>
                    <span>{formatDuration(item.maximum_duration_minutes)} max</span>
                    {when ? (
                      <>
                        <span aria-hidden>·</span>
                        <span>{when}</span>
                      </>
                    ) : null}
                  </p>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {idealFlows.length ? (
        <div className="grid gap-2 border-t border-divider pt-4">
          <h2 className="text-sm font-medium">Ideal listening flows</h2>
          <ul className="grid gap-1">
            {idealFlows.map((item) => (
              <li key={item.flow_id}>
                <Link
                  href={`/interviews/ideal/${item.flow_id}`}
                  className="grid gap-1 rounded-md border border-transparent px-3 py-3 transition-colors hover:border-divider hover:bg-surface-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="line-clamp-2 font-serif text-sm font-medium">{item.title.replace("Ideal interview · ", "")}</p>
                    <Headphones aria-hidden className="size-4 shrink-0 text-action" />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {item.covered_topic_count}/{item.topic_count} topics · {LEVEL_LABEL[item.target_level]}
                    {relativeDay(item.created_at) ? ` · ${relativeDay(item.created_at)}` : ""}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
