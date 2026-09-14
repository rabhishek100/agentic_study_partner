"use client";

import { ArrowLeft, Headphones, Loader2, Pause, Play, RotateCcw, RotateCw, SlidersHorizontal, SkipBack, SkipForward } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useIdealInterviewPlayback } from "@/hooks/use-ideal-interview-playback";
import { useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { formatInterviewTime } from "@/lib/ideal-interview-timeline";
import type { IdealInterviewFlow } from "@/lib/interview-types";
import { cn } from "@/lib/utils";

function duration(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `${minutes} min`;
}

const SPEEDS = [0.75, 0.9, 0.96, 1, 1.15, 1.25, 1.5];

type IdealPlayback = ReturnType<typeof useIdealInterviewPlayback>;

function IdealPlaybackControls({
  flow,
  playback,
}: {
  flow: IdealInterviewFlow;
  playback: IdealPlayback;
}) {
  const [scrubValue, setScrubValue] = useState<number | null>(null);
  const scrubbing = useRef(false);
  const exchange = flow.exchanges[playback.exchangeIndex];
  const progress = scrubValue ?? playback.position;

  const commitScrub = (value: number) => {
    scrubbing.current = false;
    setScrubValue(null);
    void playback.seekTo(value);
  };

  return (
    <section
      className="mb-3 shrink-0 rounded-xl border bg-popover p-4 text-popover-foreground shadow-lg"
      aria-label="Playback controls"
    >
      <div className="mb-2 flex items-center justify-between gap-3 text-xs">
        <span className="truncate font-medium">
          {exchange?.topic_label.split(" :: ").at(-1) ?? "Whole interview"} · {Math.min(flow.exchanges.length, playback.exchangeIndex + 1)} of {flow.exchanges.length}
        </span>
        <span className="shrink-0 tabular-nums text-muted-foreground">
          {formatInterviewTime(progress)} / ≈{formatInterviewTime(playback.duration)}
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={Math.max(0.01, playback.duration)}
        step={0.1}
        value={progress}
        onPointerDown={(event) => {
          scrubbing.current = true;
          setScrubValue(Number(event.currentTarget.value));
        }}
        onInput={(event) => {
          if (scrubbing.current) setScrubValue(Number(event.currentTarget.value));
        }}
        onChange={(event) => {
          const value = Number(event.currentTarget.value);
          if (scrubbing.current) setScrubValue(value);
          else commitScrub(value);
        }}
        onPointerUp={(event) => commitScrub(Number(event.currentTarget.value))}
        onPointerCancel={() => {
          scrubbing.current = false;
          setScrubValue(null);
        }}
        aria-label="Interview position"
        className="block h-5 w-full cursor-pointer accent-primary"
      />
      <div className="mt-2 flex flex-wrap items-center justify-center gap-1 sm:gap-2">
        <Button size="icon-sm" variant="ghost" aria-label="Previous exchange" disabled={playback.exchangeIndex === 0} onClick={() => playback.seekExchange(Math.max(0, playback.exchangeIndex - 1))}><SkipBack /></Button>
        <Button size="icon-sm" variant="ghost" aria-label="Rewind 15 seconds" onClick={() => playback.seekBy(-15)}><RotateCcw /></Button>
        {playback.status === "playing" || playback.status === "connecting" ? (
          <Button size="icon" variant="outline" aria-label="Pause interview" onClick={() => void playback.pause()}>{playback.status === "connecting" ? <Loader2 className="animate-spin" /> : <Pause />}</Button>
        ) : (
          <Button size="icon" variant="outline" aria-label={playback.status === "complete" ? "Replay interview" : "Play interview"} onClick={() => void playback.play()}><Play /></Button>
        )}
        <Button size="icon-sm" variant="ghost" aria-label="Forward 15 seconds" onClick={() => playback.seekBy(15)}><RotateCw /></Button>
        <Button size="icon-sm" variant="ghost" aria-label="Next exchange" disabled={playback.exchangeIndex >= flow.exchanges.length - 1} onClick={() => playback.seekExchange(Math.min(flow.exchanges.length - 1, playback.exchangeIndex + 1))}><SkipForward /></Button>
        <Select value={String(playback.settings.speed)} onValueChange={(value) => playback.applySettings({ ...playback.settings, speed: Number(value) })}>
          <SelectTrigger size="sm" className="h-8 w-[4.5rem]" aria-label="Interview speed"><SelectValue /></SelectTrigger>
          <SelectContent>{SPEEDS.map((speed) => <SelectItem key={speed} value={String(speed)}>{speed}×</SelectItem>)}</SelectContent>
        </Select>
        <Popover>
          <PopoverTrigger asChild><Button size="icon-sm" variant="ghost" aria-label="Voice customization"><SlidersHorizontal /></Button></PopoverTrigger>
          <PopoverContent align="end" className="w-[min(22rem,calc(100vw-1.5rem))] space-y-4 p-4">
            <PopoverHeader><PopoverTitle>Interview voices</PopoverTitle><PopoverDescription>Choose each role’s voice and overall delivery. Saved on this device.</PopoverDescription></PopoverHeader>
            <label className="grid gap-2 text-xs font-medium">Interviewer voice
              <Select value={playback.settings.interviewerVoice} onValueChange={(value: "voice_one" | "voice_two") => playback.applySettings({ ...playback.settings, interviewerVoice: value })}>
                <SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="voice_one">Voice one · focused</SelectItem><SelectItem value="voice_two">Voice two · warm</SelectItem></SelectContent>
              </Select>
            </label>
            <label className="grid gap-2 text-xs font-medium">Candidate voice
              <Select value={playback.settings.candidateVoice} onValueChange={(value: "voice_one" | "voice_two") => playback.applySettings({ ...playback.settings, candidateVoice: value })}>
                <SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="voice_one">Voice one · focused</SelectItem><SelectItem value="voice_two">Voice two · warm</SelectItem></SelectContent>
              </Select>
            </label>
            <label className="grid gap-2 text-xs font-medium">Delivery
              <Select value={playback.settings.delivery} onValueChange={(value: "balanced" | "calm" | "animated") => playback.applySettings({ ...playback.settings, delivery: value })}>
                <SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="balanced">Balanced</SelectItem><SelectItem value="calm">Calm</SelectItem><SelectItem value="animated">More animated</SelectItem></SelectContent>
              </Select>
            </label>
            <Button variant="outline" size="sm" className="w-full" onClick={() => playback.applySettings({ ...playback.settings, interviewerVoice: playback.settings.candidateVoice, candidateVoice: playback.settings.interviewerVoice })}>Swap interviewer and candidate</Button>
          </PopoverContent>
        </Popover>
      </div>
      {playback.error ? <p className="mt-3 text-sm text-destructive" role="alert">{playback.error}</p> : null}
    </section>
  );
}

export default function IdealInterviewPage() {
  const { flowId } = useParams<{ flowId: string }>();
  const { session, sessionLoading } = useSession();
  const [flow, setFlow] = useState<IdealInterviewFlow | null>(null);
  const [loadError, setLoadError] = useState("");
  const playback = useIdealInterviewPlayback(flowId, flow?.exchanges ?? []);

  useEffect(() => {
    if (!session) return;
    void apiFetch<IdealInterviewFlow>(`/ideal-interviews/${flowId}`)
      .then(setFlow)
      .catch((failure: Error) => setLoadError(failure.message || "Could not load this interview."));
  }, [flowId, session]);

  const exchange = flow?.exchanges[playback.exchangeIndex];

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
                  onClick={() => playback.seekExchange(item.exchange_index)}
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
      <main className="min-h-0 flex-1 overflow-hidden px-4 sm:px-8">
        <div className="mx-auto flex h-full max-w-4xl flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto py-6">
            <Link href="/interviews" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" />Interviews</Link>
            {loadError ? <Alert variant="destructive" className="mt-5"><AlertDescription>{loadError}</AlertDescription></Alert> : !flow ? (
              <div className="mt-12 flex items-center justify-center gap-3 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Loading the interview transcript…</div>
            ) : (
              <div className="mt-5 grid gap-6 pb-6">
                <header>
                  <div className="flex flex-wrap items-center gap-2"><Badge variant="secondary">{flow.interview_format.replace("_", " ")}</Badge><Badge variant="outline">{flow.target_level}</Badge></div>
                  <h1 className="mt-3 font-serif text-3xl font-semibold tracking-tight">{flow.title}</h1>
                  <p className="mt-2 text-sm text-muted-foreground">{flow.source_title} · {flow.covered_topic_count}/{flow.topic_count} source topics · about {duration(flow.estimated_duration_seconds)}</p>
                </header>

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
          {flow ? <IdealPlaybackControls flow={flow} playback={playback} /> : null}
        </div>
      </main>
    </AppShell>
  );
}
