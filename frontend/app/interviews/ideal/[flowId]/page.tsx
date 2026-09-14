"use client";

import { ArrowLeft, Headphones, Loader2, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useIdealInterviewPlayback } from "@/hooks/use-ideal-interview-playback";
import { useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import type { IdealInterviewFlow } from "@/lib/interview-types";
import { cn } from "@/lib/utils";

function duration(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `${minutes} min`;
}

export default function IdealInterviewPage() {
  const { flowId } = useParams<{ flowId: string }>();
  const { session, sessionLoading } = useSession();
  const [flow, setFlow] = useState<IdealInterviewFlow | null>(null);
  const [loadError, setLoadError] = useState("");
  const playback = useIdealInterviewPlayback(flowId);

  useEffect(() => {
    if (!session) return;
    void apiFetch<IdealInterviewFlow>(`/ideal-interviews/${flowId}`)
      .then(setFlow)
      .catch((failure: Error) => setLoadError(failure.message || "Could not load this interview."));
  }, [flowId, session]);

  const exchange = flow?.exchanges[playback.exchangeIndex];
  const progress = useMemo(() => {
    if (!flow) return 0;
    if (playback.status === "complete") return 100;
    return Math.min(100, ((playback.exchangeIndex + (playback.speaker === "candidate" ? 0.5 : 0)) / flow.exchanges.length) * 100);
  }, [flow, playback.exchangeIndex, playback.speaker, playback.status]);

  if (sessionLoading) return <div className="grid h-dvh place-items-center"><Loader2 className="animate-spin" /></div>;
  if (!session) return <div className="relative grid h-dvh place-items-center p-6"><div className="absolute right-3 top-3"><ThemeToggle /></div><AuthGate /></div>;

  return (
    <AppShell
      section="interviews"
      status={<span>Ideal chapter interview · listen-only</span>}
      account={<AccountMenu email={session.user.email} />}
      rail={flow ? (
        <nav className="h-full overflow-y-auto p-3" aria-label="Interview chapters">
          <p className="px-2 pb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Flow</p>
          <ol className="space-y-1">
            {flow.exchanges.map((item) => (
              <li key={item.exchange_index}>
                <button
                  type="button"
                  onClick={() => playback.seek(item.exchange_index)}
                  className={cn(
                    "w-full rounded-md px-2 py-2 text-left text-xs leading-5 hover:bg-surface-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-action",
                    item.exchange_index === playback.exchangeIndex && "bg-wash text-foreground",
                  )}
                  aria-current={item.exchange_index === playback.exchangeIndex ? "step" : undefined}
                >
                  <span className="block capitalize text-muted-foreground">{item.phase.replace("_", " ")}</span>
                  <span className="line-clamp-2">{item.topic_label.split(" :: ").at(-1)}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>
      ) : null}
    >
      <main className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto max-w-4xl">
          <Link href="/interviews" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" />Interviews</Link>
          {loadError ? <Alert variant="destructive" className="mt-5"><AlertDescription>{loadError}</AlertDescription></Alert> : !flow ? (
            <div className="mt-12 flex items-center justify-center gap-3 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Loading the interview transcript…</div>
          ) : (
            <div className="mt-5 grid gap-6">
              <header>
                <div className="flex flex-wrap items-center gap-2"><Badge variant="secondary">{flow.interview_format.replace("_", " ")}</Badge><Badge variant="outline">{flow.target_level}</Badge></div>
                <h1 className="mt-3 font-serif text-3xl font-semibold tracking-tight">{flow.title}</h1>
                <p className="mt-2 text-sm text-muted-foreground">{flow.source_title} · {flow.covered_topic_count}/{flow.topic_count} source topics · about {duration(flow.estimated_duration_seconds)}</p>
              </header>

              <section className="sticky top-3 z-sticky rounded-xl border bg-popover p-4 text-popover-foreground shadow-sm" aria-label="Playback controls">
                <div className="flex items-center gap-3">
                  <Button size="icon" variant="outline" aria-label="Previous exchange" disabled={playback.exchangeIndex === 0} onClick={() => playback.seek(Math.max(0, playback.exchangeIndex - 1))}><SkipBack /></Button>
                  {playback.status === "playing" || playback.status === "connecting" ? (
                    <Button className="min-w-28" onClick={() => void playback.pause()}><Pause />Pause</Button>
                  ) : (
                    <Button className="min-w-28" onClick={() => void playback.play()}><Play /> {playback.status === "complete" ? "Replay" : "Play"}</Button>
                  )}
                  <Button size="icon" variant="outline" aria-label="Next exchange" disabled={playback.exchangeIndex >= flow.exchanges.length - 1} onClick={() => playback.seek(Math.min(flow.exchanges.length - 1, playback.exchangeIndex + 1))}><SkipForward /></Button>
                  <div className="min-w-0 flex-1"><Progress value={progress} /><p className="mt-1 text-right text-xs text-muted-foreground">{Math.min(flow.exchanges.length, playback.exchangeIndex + 1)} of {flow.exchanges.length}</p></div>
                </div>
                {playback.error ? <p className="mt-3 text-sm text-destructive" role="alert">{playback.error}</p> : null}
              </section>

              {exchange ? (
                <section className="space-y-5" aria-live="polite">
                  <div className={cn("max-w-[88%] rounded-2xl rounded-tl-sm border bg-card p-5", playback.speaker === "interviewer" && playback.status === "playing" && "ring-2 ring-action")}>
                    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground"><Headphones className="size-4" />Interviewer</p>
                    <p className="mt-3 text-base leading-7">{exchange.interviewer_text}</p>
                  </div>
                  <div className={cn("ml-auto max-w-[92%] rounded-2xl rounded-tr-sm bg-wash p-5", playback.speaker === "candidate" && playback.status === "playing" && "ring-2 ring-positive")}>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Candidate</p>
                    <p className="mt-3 text-base leading-7">{exchange.candidate_text}</p>
                  </div>
                  <details className="rounded-lg border bg-surface px-4 py-3 text-sm">
                    <summary className="cursor-pointer font-medium">Grounding · {exchange.citations.length} citation{exchange.citations.length === 1 ? "" : "s"}</summary>
                    <ul className="mt-3 space-y-1 text-muted-foreground">
                      {exchange.citations.map((citation) => <li key={citation.marker}>{citation.marker} · page {citation.page}</li>)}
                    </ul>
                  </details>
                </section>
              ) : null}
            </div>
          )}
        </div>
      </main>
    </AppShell>
  );
}
