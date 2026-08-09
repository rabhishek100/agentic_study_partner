"use client";

import {
  ArrowLeft,
  CheckCircle2,
  Clock3,
  ExternalLink,
  LogOut,
  Mic,
  MonitorUp,
  Pause,
  Play,
  Send,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  primeInterviewerSpeech,
  useInterviewerSpeech,
} from "@/hooks/use-interviewer-speech";
import {
  primeInterviewAudio,
  releasePrimedInterviewAudio,
  useInterviewVoice,
} from "@/hooks/use-interview-voice";
import { useScreenShare } from "@/hooks/use-screen-share";
import { signOut, useSession } from "@/hooks/use-session";
import { ApiError, apiFetch, errorDetail, uploadUrl } from "@/lib/api";
import { transcribeInterviewRecording } from "@/lib/dictation";
import {
  appendTranscriptSegment,
  pendingTurn,
  type AnswerEvaluation,
  type InterviewReport,
  type InterviewSession,
  type InterviewTurn,
} from "@/lib/interview-types";
import { accessToken } from "@/lib/supabase";
import { cn } from "@/lib/utils";

const INR_PER_USD_ESTIMATE = Number(process.env.NEXT_PUBLIC_USD_INR_RATE ?? "90");
const SYSTEM_DEFAULT_MICROPHONE = "__system_default__";

function clock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remainder = safe % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function scoreColor(score: number): string {
  if (score >= 4) return "bg-emerald-500";
  if (score >= 3) return "bg-amber-500";
  return "bg-rose-500";
}

function Feedback({
  evaluation,
  showConcise = true,
}: {
  evaluation: AnswerEvaluation;
  showConcise?: boolean;
}) {
  return (
    <div className="mt-4 rounded-xl border border-primary/15 bg-primary/[0.035] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{titleCase(evaluation.classification)}</Badge>
        <span className="text-xs text-muted-foreground">Technical {evaluation.scores.technical_correctness}/5 · Depth {evaluation.scores.depth_completeness}/5</span>
      </div>
      {showConcise && evaluation.concise_feedback ? <p className="mt-3 text-sm leading-6">{evaluation.concise_feedback}</p> : null}
      {evaluation.gaps.length ? <div className="mt-3"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Improve</p><ul className="mt-1 space-y-1 text-sm">{evaluation.gaps.map((gap) => <li key={gap}>• {gap}</li>)}</ul></div> : null}
    </div>
  );
}

function InterviewExchange({
  turn,
  reacting,
  reactionLoading,
  reactionSpeaking,
}: {
  turn: InterviewTurn;
  reacting: boolean;
  reactionLoading: boolean;
  reactionSpeaking: boolean;
}) {
  return (
    <div className="space-y-3">
      <div className="max-w-[88%] rounded-2xl rounded-tl-sm border bg-card p-4">
        <p className="text-xs font-medium text-primary">Interviewer</p>
        <p className="mt-1.5 text-sm leading-6">{turn.question.text}</p>
      </div>
      <div className="ml-auto max-w-[88%] rounded-2xl rounded-tr-sm bg-primary px-4 py-3 text-primary-foreground">
        <p className="text-xs font-medium opacity-70">You</p>
        <p className="mt-1.5 text-sm leading-6">{turn.answer_text}</p>
      </div>
      {turn.interviewer_reaction ? (
        <div className="max-w-[88%] rounded-2xl rounded-tl-sm border border-primary/20 bg-primary/[0.035] p-4">
          <p className="flex items-center gap-1.5 text-xs font-medium text-primary">
            <Volume2 aria-hidden className="size-3.5" />
            Interviewer response
          </p>
          <p className="mt-1.5 text-sm leading-6">{turn.interviewer_reaction}</p>
          {reacting ? (
            <p className="mt-2 text-xs text-muted-foreground">
              {reactionLoading
                ? "Preparing response…"
                : reactionSpeaking
                  ? "Speaking…"
                  : "Finishing response…"}
            </p>
          ) : null}
        </div>
      ) : null}
      {turn.evaluation ? (
        <Feedback evaluation={turn.evaluation} showConcise={false} />
      ) : null}
    </div>
  );
}

function SessionReportView({ report }: { report: InterviewReport }) {
  const { session } = report;
  const dimensions = Object.entries(session.metrics.dimension_scores);
  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-4 py-8 sm:px-8">
      <div className="rounded-2xl border bg-card p-6 sm:p-8">
        <Badge variant="secondary"><CheckCircle2 aria-hidden /> Interview complete</Badge>
        <div className="mt-5 flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
          <div><h1 className="font-heading text-3xl font-semibold">Your interview report</h1><p className="mt-2 text-muted-foreground">{session.title.replace("Interview · ", "")} · {session.metrics.questions_answered} answered</p></div>
          <div className="rounded-xl bg-primary px-6 py-4 text-primary-foreground"><p className="text-xs font-medium uppercase tracking-wider opacity-75">Overall</p><p className="mt-1 font-heading text-4xl font-semibold">{session.metrics.overall_score?.toFixed(1) ?? "—"}<span className="text-lg opacity-75">/5</span></p></div>
        </div>
        <div className="mt-6 grid gap-3 sm:grid-cols-3"><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Coverage</p><p className="mt-1 text-sm font-medium">{session.metrics.topics_covered} of {session.metrics.topics_required} topics</p></div><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Active time</p><p className="mt-1 text-sm font-medium">{clock(session.elapsed_seconds)}</p></div><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Provider cost</p><p className="mt-1 text-sm font-medium">${session.total_cost_usd.toFixed(4)} · ≈₹{(session.total_cost_usd * INR_PER_USD_ESTIMATE).toFixed(1)}</p></div></div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[0.8fr_1.2fr]">
        <Card><CardHeader><CardTitle>Score profile</CardTitle></CardHeader><CardContent className="space-y-4">{dimensions.map(([name, score]) => <div key={name}><div className="mb-1.5 flex justify-between gap-3 text-sm"><span>{titleCase(name)}</span><span className="font-medium tabular-nums">{score.toFixed(1)}</span></div><div className="h-2 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full", scoreColor(score))} style={{ width: `${score * 20}%` }} /></div></div>)}</CardContent></Card>
        <Card><CardHeader><CardTitle>What to do next</CardTitle></CardHeader><CardContent><p className="text-sm leading-6 text-muted-foreground">{report.evidence_confidence}</p>{session.metrics.strengths.length ? <div className="mt-4"><p className="text-sm font-medium">Strengths</p><ul className="mt-2 space-y-1.5 text-sm">{session.metrics.strengths.map((item) => <li key={item}>• {item}</li>)}</ul></div> : null}{report.suggested_next_steps.length ? <div className="mt-4"><p className="text-sm font-medium">Focused revision</p><ul className="mt-2 space-y-1.5 text-sm">{report.suggested_next_steps.map((item) => <li key={item}>• {item}</li>)}</ul></div> : null}</CardContent></Card>
      </div>

      <div className="space-y-4"><h2 className="font-heading text-xl font-semibold">Question review</h2>{session.turns.filter((turn) => turn.answer_text).map((turn) => <Card key={turn.turn_index}><CardContent className="p-5 sm:p-6"><div className="flex items-start gap-3"><span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{turn.turn_index + 1}</span><div className="min-w-0 flex-1"><p className="font-medium leading-6">{turn.question.text}</p><p className="mt-3 rounded-lg bg-muted/70 p-3 text-sm leading-6">{turn.answer_text}</p>{turn.evaluation ? <><Feedback evaluation={turn.evaluation} /><div className="mt-4"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Recommended answer</p><p className="mt-2 text-sm leading-6">{turn.evaluation.recommended_answer}</p></div></> : null}<div className="mt-4 flex flex-wrap gap-2">{turn.citations.map((citation) => citation.page && session.book_id ? <Link key={citation.marker} href={`/?book=${session.book_id}&page=${citation.page}`} className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs hover:bg-accent">{citation.marker} · p. {citation.page}<ExternalLink aria-hidden className="size-3" /></Link> : citation.start_ms !== null && session.video_id ? <Link key={citation.marker} href={`/videos/${session.video_id}?t=${Math.floor(citation.start_ms / 1000)}`} className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs hover:bg-accent">{citation.marker} · {clock(citation.start_ms / 1000)}<ExternalLink aria-hidden className="size-3" /></Link> : null)}{turn.web_sources.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs hover:bg-accent">Web {source.rank}<ExternalLink aria-hidden className="size-3" /></a>)}</div></div></div></CardContent></Card>)}</div>
    </div>
  );
}

export default function InterviewWorkspace() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { session: authSession, sessionLoading } = useSession();
  const [interview, setInterview] = useState<InterviewSession | null>(null);
  const [report, setReport] = useState<InterviewReport | null>(null);
  const [answer, setAnswer] = useState("");
  const [transcriptCorrected, setTranscriptCorrected] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [screenBusy, setScreenBusy] = useState(false);
  const [listeningPaused, setListeningPaused] = useState(false);
  const [transitionTurnIndex, setTransitionTurnIndex] = useState<number | null>(null);
  const [error, setError] = useState("");
  const loadedAtRef = useRef(Date.now());
  const lastSpokenRef = useRef<number | null>(null);
  const speech = useInterviewerSpeech();
  const screen = useScreenShare();

  const pending = interview ? pendingTurn(interview) : null;
  const current = transitionTurnIndex === null ? pending : null;

  const submitAnswer = useCallback(async (text: string, corrected: boolean) => {
    const value = text.trim();
    if (!value || !current || !interview || interview.status !== "active" || busy) return;
    setBusy(true);
    setError("");
    try {
      const answeredTurnIndex = current.turn_index;
      const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/answers`, { method: "POST", body: JSON.stringify({ answer_text: value, transcript_corrected: corrected }) });
      const settledTurn = updated.turns.find(
        (turn) => turn.turn_index === answeredTurnIndex,
      );
      const reaction = settledTurn?.interviewer_reaction?.trim() ?? "";
      loadedAtRef.current = Date.now();
      if (reaction) setTransitionTurnIndex(answeredTurnIndex);
      setInterview(updated);
      setAnswer("");
      setTranscriptCorrected(false);
      setBusy(false);
      if (reaction) {
        await speech.speakReaction(sessionId, answeredTurnIndex, reaction);
        setTransitionTurnIndex(null);
      }
      if (["completed", "abandoned"].includes(updated.status)) {
        setReport(await apiFetch<InterviewReport>(`/interviews/${sessionId}/report`));
      }
    } catch (failure) {
      setError((failure as Error).message || "That answer could not be evaluated.");
    } finally {
      setBusy(false);
    }
  }, [busy, current, interview, sessionId, speech.speakReaction]);

  const handleRecording = useCallback(async (recording: Blob) => {
    const transcript = await transcribeInterviewRecording(recording, sessionId);
    if (!transcript.trim()) throw new Error("No speech was recorded.");
    setAnswer((currentAnswer) => appendTranscriptSegment(currentAnswer, transcript));
  }, [sessionId]);

  const voice = useInterviewVoice({ onRecording: handleRecording, onVoiceStart: speech.stop });

  const load = useCallback(async () => {
    try {
      // Reloading or remounting must preserve a live interview. The previous
      // implementation converted every active reload into a pause, which
      // removed the composer and disabled narration even though the candidate
      // never selected Pause.
      const found = await apiFetch<InterviewSession>(`/interviews/${sessionId}`);
      loadedAtRef.current = Date.now();
      setInterview(found);
      if (["completed", "abandoned"].includes(found.status)) {
        setReport(await apiFetch<InterviewReport>(`/interviews/${sessionId}/report`));
      }
    } catch (failure) {
      setError((failure as Error).message || "Could not load this interview.");
    }
  }, [sessionId]);

  useEffect(() => { if (authSession) void load(); }, [authSession, load]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(timer); }, []);

  useEffect(() => {
    const shouldListen =
      interview?.status === "active" &&
      current !== null &&
      !busy &&
      !listeningPaused &&
      !speech.loading &&
      !speech.speaking;
    if (!shouldListen) {
      if (voice.status !== "idle") voice.stop();
      return;
    }
    if (voice.supported && voice.status === "idle" && !voice.error) {
      void voice.start();
    }
  }, [busy, current, interview?.status, listeningPaused, speech.loading, speech.speaking, voice.error, voice.start, voice.status, voice.stop, voice.supported]);

  useEffect(() => {
    if (
      interview?.status !== "active" ||
      !current ||
      current.turn_index === lastSpokenRef.current
    ) return;
    lastSpokenRef.current = current.turn_index;
    void speech.speak(sessionId, current.turn_index, current.question.text);
  }, [current, interview?.status, sessionId, speech.speak]);

  const elapsed = useMemo(() => {
    if (!interview) return 0;
    return interview.elapsed_seconds + (interview.status === "active" ? Math.floor((now - loadedAtRef.current) / 1000) : 0);
  }, [interview, now]);
  const remaining = Math.max(0, (interview?.maximum_duration_minutes ?? 0) * 60 - elapsed);
  const coverage = interview?.metrics.topics_required ? (interview.metrics.topics_covered / interview.metrics.topics_required) * 100 : interview?.checkpoint.topics.length ? (interview.checkpoint.topics.filter((topic) => topic.required && topic.completed).length / interview.checkpoint.topics.filter((topic) => topic.required).length) * 100 : 0;

  const pause = useCallback(async () => {
    voice.stop(); speech.stop(); setBusy(true);
    try { const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/pause`, { method: "POST" }); loadedAtRef.current = Date.now(); setInterview(updated); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }, [sessionId, speech.stop, voice.stop]);
  const resume = useCallback(async () => {
    primeInterviewAudio();
    primeInterviewerSpeech();
    lastSpokenRef.current = null;
    setListeningPaused(false);
    setBusy(true);
    try { const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/resume`, { method: "POST" }); loadedAtRef.current = Date.now(); setInterview(updated); }
    catch (failure) { releasePrimedInterviewAudio(); setError((failure as Error).message); }
    finally { setBusy(false); }
  }, [sessionId]);
  const finish = useCallback(async () => {
    voice.stop(); speech.stop(); setBusy(true);
    try { const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/finish`, { method: "POST" }); setInterview(updated); setReport(await apiFetch<InterviewReport>(`/interviews/${sessionId}/report`)); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }, [sessionId, speech.stop, voice.stop]);

  useEffect(() => {
    if (!interview || interview.status !== "active" || busy) return;
    const ceiling = interview.maximum_duration_minutes * 60;
    if (elapsed >= ceiling + 120) void finish();
  }, [busy, elapsed, finish, interview]);

  const submitScreen = useCallback(async () => {
    setScreenBusy(true); setError("");
    try {
      const blob = await screen.capture();
      const token = await accessToken();
      const response = await fetch(uploadUrl(`/interviews/${sessionId}/screen-checkpoints`), { method: "POST", headers: { "Content-Type": blob.type, ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: blob });
      if (!response.ok) throw new ApiError(await errorDetail(response), response.status);
      setInterview((await response.json()) as InterviewSession);
    } catch (failure) { setError((failure as Error).message || "Screen checkpoint failed."); }
    finally { setScreenBusy(false); }
  }, [screen, sessionId]);

  if (sessionLoading || (authSession && !interview && !error)) return <div className="grid h-dvh place-items-center"><Skeleton className="h-6 w-56" /></div>;
  if (!authSession) return <div className="relative grid h-dvh place-items-center p-6"><div className="absolute right-3 top-3"><ThemeToggle /></div><AuthGate /></div>;
  if (!interview) return <div className="grid h-dvh place-items-center p-6"><Alert variant="destructive" className="max-w-lg"><AlertDescription>{error || "Interview not found."}</AlertDescription></Alert></div>;

  return (
    <AppShell
      nav={<SectionNav active="interviews" />}
      status={<span>{interview.status === "active" ? (remaining > 0 ? `${clock(remaining)} remaining` : "Finish your current answer") : titleCase(interview.status)}</span>}
      account={<DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="sm" className="max-w-44"><span className="truncate">{authSession.user.email}</span></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuLabel className="font-normal text-muted-foreground">Signed in</DropdownMenuLabel><DropdownMenuSeparator /><DropdownMenuItem onSelect={() => signOut()}><LogOut aria-hidden />Sign out</DropdownMenuItem></DropdownMenuContent></DropdownMenu>}
      rail={<div className="flex h-full flex-col overflow-y-auto p-4"><div className="mb-5 sm:hidden"><SectionNav active="interviews" /></div><Link href="/interviews" className="mb-5 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft aria-hidden className="size-4" />All interviews</Link><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Scope</p><h2 className="mt-2 font-heading text-base font-medium">{interview.title.replace("Interview · ", "")}</h2><p className="mt-1 text-xs text-muted-foreground">{interview.source_title}</p><div className="mt-5 space-y-3 border-t pt-4"><div className="flex justify-between text-xs"><span className="text-muted-foreground">Format</span><span className="capitalize">{interview.interview_format.replace("_", " ")}</span></div><div className="flex justify-between text-xs"><span className="text-muted-foreground">Level</span><span className="capitalize">{interview.target_level}</span></div><div className="flex justify-between text-xs"><span className="text-muted-foreground">Mode</span><span className="capitalize">{interview.feedback_mode}</span></div></div><div className="mt-6"><div className="mb-2 flex justify-between text-xs"><span>Source coverage</span><span>{Math.round(coverage)}%</span></div><Progress value={coverage} /></div><p className="mt-4 text-xs leading-5 text-muted-foreground">Topic order and future questions remain hidden. The interview finishes early when meaningful coverage is complete.</p></div>}
    >
      {report ? <SessionReportView report={report} /> : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
            {error || voice.error || speech.error || screen.error ? <Alert variant="destructive"><AlertDescription>{error || voice.error || speech.error || screen.error}</AlertDescription></Alert> : null}

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3">
              <div className="flex items-center gap-3"><div className={cn("size-2.5 rounded-full", interview.status === "active" ? "bg-emerald-500 motion-safe:animate-pulse" : "bg-amber-500")} /><div><p className="text-sm font-medium">{titleCase(interview.status)}</p><p className="text-xs text-muted-foreground">{clock(elapsed)} elapsed · {remaining > 0 ? `${clock(remaining)} remaining` : "current answer only"}</p></div></div>
              <div className="flex items-center gap-2">{interview.status === "active" ? <Button variant="outline" size="sm" onClick={() => void pause()} disabled={busy}><Pause aria-hidden />Pause</Button> : <Button variant="outline" size="sm" onClick={() => void resume()} disabled={busy}><Play aria-hidden />Resume</Button>}<Button variant="ghost" size="sm" onClick={() => void finish()} disabled={busy}>End interview</Button></div>
            </div>

            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
              <div className="space-y-5">
                <div className="space-y-4">
                  {interview.turns
                    .filter((turn) => turn.answer_text)
                    .map((turn: InterviewTurn) => (
                      <InterviewExchange
                        key={turn.turn_index}
                        turn={turn}
                        reacting={transitionTurnIndex === turn.turn_index}
                        reactionLoading={speech.loading}
                        reactionSpeaking={speech.speaking}
                      />
                    ))}
                </div>

                {current ? (
                  <Card className="overflow-hidden border-primary/20 shadow-sm">
                    <div className="h-1 bg-primary" />
                    <CardHeader className="pb-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Badge variant="outline">Question {current.turn_index + 1}</Badge>
                        <Badge variant="secondary" className="capitalize">{current.question.kind.replace("_", " ")}</Badge>
                      </div>
                      <CardTitle className="mt-3 text-xl leading-8 sm:text-2xl">{current.question.text}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={speech.loading}
                          onClick={() => {
                            if (speech.speaking) speech.stop();
                            else {
                              lastSpokenRef.current = current.turn_index;
                              void speech.speak(sessionId, current.turn_index, current.question.text);
                            }
                          }}
                        >
                          {speech.speaking ? <VolumeX aria-hidden /> : <Volume2 aria-hidden />}
                          {speech.speaking ? "Stop voice" : speech.loading ? "Preparing voice…" : "Hear question again"}
                        </Button>
                        {voice.supported ? (
                          <Button
                            variant={voice.status === "idle" ? "outline" : "secondary"}
                            size="sm"
                            disabled={speech.loading || speech.speaking}
                            onClick={() => {
                              if (voice.status === "idle") {
                                setListeningPaused(false);
                                void voice.start();
                              } else {
                                setListeningPaused(true);
                                voice.stop();
                              }
                            }}
                          >
                            <Mic aria-hidden />
                            {voice.status === "idle" ? "Start listening" : "Pause listening"}
                          </Button>
                        ) : null}
                      </div>
                      {current.screen_observation ? <Alert className="mt-4"><MonitorUp aria-hidden /><AlertDescription>Screen checkpoint received: {current.screen_observation.summary}</AlertDescription></Alert> : null}
                    </CardContent>
                  </Card>
                ) : null}

                {current && interview.status === "active" ? (
                  <Card id="question">
                    <CardContent className="p-4">
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className={cn("size-2 rounded-full", voice.status === "recording" ? "bg-destructive motion-safe:animate-pulse" : voice.status === "listening" ? "bg-emerald-500" : "bg-muted-foreground/40")} />
                          {voice.status !== "idle" ? (
                            <span
                              className="h-1.5 w-16 overflow-hidden rounded-full bg-muted"
                              role="meter"
                              aria-label="Live microphone level"
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={Math.round(voice.inputLevel * 100)}
                            >
                              <span
                                className="block h-full rounded-full bg-emerald-500 transition-[width] duration-75"
                                style={{ width: `${Math.round(voice.inputLevel * 100)}%` }}
                              />
                            </span>
                          ) : null}
                          <span className="text-xs text-muted-foreground">
                            {speech.loading || speech.speaking
                              ? "Interviewer speaking — listening starts automatically next"
                              : voice.status === "recording"
                              ? "Capturing this part of your answer…"
                              : voice.status === "processing"
                                ? "Adding speech to your draft — keep speaking when ready"
                                : voice.status === "listening"
                                  ? voice.mode === "automatic"
                                    ? "Listening continuously — pauses only update the draft"
                                    : "Hold the mic button to speak"
                                  : "Type your answer or start listening"}
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <Select
                            value={voice.microphones.selectedId ?? SYSTEM_DEFAULT_MICROPHONE}
                            onValueChange={(value) => {
                              voice.stop();
                              voice.microphones.select(
                                value === SYSTEM_DEFAULT_MICROPHONE ? null : value,
                              );
                              setListeningPaused(false);
                            }}
                          >
                            <SelectTrigger className="h-8 w-44 text-xs" aria-label="Interview microphone"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value={SYSTEM_DEFAULT_MICROPHONE}>System default mic</SelectItem>
                              {voice.microphones.devices.map((device) => (
                                <SelectItem key={device.deviceId} value={device.deviceId}>{device.label}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {voice.status !== "idle" ? (
                            <Select value={voice.mode} onValueChange={(value) => voice.setMode(value as "automatic" | "push_to_talk")}>
                              <SelectTrigger className="h-8 w-44 text-xs"><SelectValue /></SelectTrigger>
                              <SelectContent>
                                <SelectItem value="automatic">Continuous listening</SelectItem>
                                <SelectItem value="push_to_talk">Push to talk</SelectItem>
                              </SelectContent>
                            </Select>
                          ) : null}
                        </div>
                      </div>
                      <Textarea
                        value={answer}
                        onChange={(event) => {
                          setAnswer(event.target.value);
                          setTranscriptCorrected(true);
                        }}
                        placeholder="Speak or type your answer. Nothing is sent until you choose Send answer."
                        className="min-h-32 resize-y text-base leading-6"
                        disabled={busy}
                      />
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        Thinking pauses are safe. Each spoken segment is appended here, and you decide when the complete answer is ready.
                      </p>
                      <div className="mt-3 flex items-center justify-between gap-3">
                        {voice.mode === "push_to_talk" && voice.status !== "idle" ? (
                          <Button type="button" variant="secondary" onPointerDown={voice.beginPush} onPointerUp={voice.endPush} onPointerCancel={voice.endPush}>
                            <Mic aria-hidden />Hold to talk
                          </Button>
                        ) : <span />}
                        <Button
                          disabled={
                            !answer.trim() ||
                            busy ||
                            voice.status === "recording" ||
                            voice.status === "processing"
                          }
                          onClick={() => void submitAnswer(answer, transcriptCorrected)}
                        >
                          {busy
                            ? "Evaluating…"
                            : voice.status === "recording"
                              ? "Finish speaking…"
                              : voice.status === "processing"
                                ? "Finishing transcript…"
                                : "Send answer"}
                          <Send aria-hidden />
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ) : current && interview.status === "paused" ? (
                  <Card className="border-amber-500/30 bg-amber-500/[0.04]">
                    <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="font-medium">This interview is paused</p>
                        <p className="mt-1 text-sm leading-6 text-muted-foreground">
                          Resume to replay the current question and restore automatic listening.
                        </p>
                      </div>
                      <Button onClick={() => void resume()} disabled={busy}>
                        <Play aria-hidden />
                        {busy ? "Resuming…" : "Resume interview"}
                      </Button>
                    </CardContent>
                  </Card>
                ) : null}
              </div>

              <aside className="space-y-4">
                <Card><CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><MonitorUp aria-hidden className="size-4" />Screen checkpoint</CardTitle></CardHeader><CardContent><p className="text-xs leading-5 text-muted-foreground">Share locally, then submit one still when your code, diagram, or whiteboard is ready. Nothing is continuously uploaded.</p><video ref={screen.videoRef} muted playsInline className={cn("mt-3 aspect-video w-full rounded-lg border bg-black object-contain", !screen.sharing && "hidden")} /> <div className="mt-3 flex gap-2">{!screen.sharing ? <Button variant="outline" size="sm" className="w-full" disabled={!screen.supported || interview.status !== "active"} onClick={() => void screen.start()}><MonitorUp aria-hidden />Share screen</Button> : <><Button size="sm" className="flex-1" disabled={screenBusy} onClick={() => void submitScreen()}>{screenBusy ? "Analyzing…" : "Submit screen"}</Button><Button variant="outline" size="icon-sm" aria-label="Stop sharing" onClick={screen.stop}><Square aria-hidden /></Button></>}</div></CardContent></Card>
                <Card><CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><Clock3 aria-hidden className="size-4" />Session budget</CardTitle></CardHeader><CardContent><div className="flex items-end justify-between"><div><p className="font-heading text-2xl font-semibold">${interview.total_cost_usd.toFixed(4)}</p><p className="text-xs text-muted-foreground">≈₹{(interview.total_cost_usd * INR_PER_USD_ESTIMATE).toFixed(1)} provider cost</p></div><span className="text-xs text-muted-foreground">Target ₹5–10</span></div><Progress className="mt-3" value={Math.min(100, (interview.total_cost_usd * INR_PER_USD_ESTIMATE / 10) * 100)} /><p className="mt-3 text-xs leading-5 text-muted-foreground">Raw audio and screen images are discarded after processing.</p></CardContent></Card>
              </aside>
            </div>
          </div>
        </div>
                  )}
    </AppShell>
  );
}
