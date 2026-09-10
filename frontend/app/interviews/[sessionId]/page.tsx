"use client";

import {
  ArrowLeft,
  CheckCircle2,
  Clock3,
  ExternalLink,
  Loader2,
  MessageCircleQuestion,
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

import { AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import {
  emptyPythonExecution,
  PythonCodingWorkspace,
} from "@/components/interviews/python-coding-workspace";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel, PanelContent, PanelHeader, PanelTitle } from "@/components/ui/panel";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { useLivekitInterview } from "@/hooks/use-livekit-interview";
import { LIVEKIT_INTERVIEWS } from "@/lib/livekit-interview";
import { useSession } from "@/hooks/use-session";
import { ApiError, apiFetch, errorDetail, uploadUrl } from "@/lib/api";
import { transcribeInterviewRecording } from "@/lib/dictation";
import {
  describeInterviewActivity,
  type InterviewActivity,
  type InterviewOperation,
} from "@/lib/interview-activity";
import {
  appendTranscriptSegment,
  pendingTurn,
  spokenInterviewQuestion,
  workSampleLabel,
  type AnswerEvaluation,
  type InterviewReport,
  type InterviewSession,
  type InterviewTurn,
  type PythonExecutionResult,
} from "@/lib/interview-types";
import { accessToken } from "@/lib/supabase";
import { cn } from "@/lib/utils";

const INR_PER_USD_ESTIMATE = Number(process.env.NEXT_PUBLIC_USD_INR_RATE ?? "90");
const SYSTEM_DEFAULT_MICROPHONE = "__system_default__";
const ANSWER_SUBMISSION_TIMEOUT_MS = 50_000;
const TRANSCRIPTION_TIMEOUT_MS = 30_000;
const SUBMISSION_RECONCILIATION_DELAYS_MS = [0, 2_000, 4_000, 6_000] as const;

interface CodingDraft {
  key: string;
  code: string;
  scratchTests: string;
  execution: PythonExecutionResult;
}

function codingDraftKey(sessionId: string, turnIndex: number): string {
  return `interview-coding-draft:${sessionId}:${turnIndex}`;
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

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
  if (score >= 4) return "bg-positive";
  if (score >= 3) return "bg-warning";
  return "bg-destructive";
}

function ActivityStatus({
  activity,
  elapsedSeconds,
}: {
  activity: InterviewActivity;
  elapsedSeconds: number | null;
}) {
  const working = activity.tone === "working";
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border bg-card",
        working && "border-action bg-surface",
        activity.tone === "live" && "border-positive bg-wash",
        activity.tone === "paused" && "border-warning bg-warning-wash",
      )}
    >
      {working ? (
        <div className="h-1 w-full bg-wash">
          <div className="h-full w-full bg-wash motion-safe:animate-pulse" />
        </div>
      ) : null}
      <div className="flex items-start gap-3 px-4 py-4">
        <div className="mt-1 grid size-8 shrink-0 place-items-center rounded-full bg-muted">
          {working ? (
            <Loader2 aria-hidden className="size-4 animate-spin text-primary motion-reduce:animate-none" />
          ) : activity.tone === "live" ? (
            <span className="size-2.5 rounded-full bg-positive motion-safe:animate-pulse" />
          ) : activity.tone === "paused" ? (
            <Pause aria-hidden className="size-4 text-warning" />
          ) : (
            <CheckCircle2 aria-hidden className="size-4 text-positive" />
          )}
        </div>
        <div className="min-w-0 flex-1" role="status" aria-live="polite" aria-atomic="true">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold">{activity.title}</p>
            {working && elapsedSeconds !== null ? (
              <span className="text-xs tabular-nums text-muted-foreground" aria-hidden="true">
                {elapsedSeconds < 2 ? "Just started" : `${elapsedSeconds}s elapsed`}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{activity.detail}</p>
          {activity.stages?.length ? (
            <div className="mt-2 flex flex-wrap gap-2" aria-label="Work included in this request">
              {activity.stages.map((stage) => (
                <span key={stage} className="rounded-full border bg-canvas px-2 py-1 text-xs text-muted-foreground">
                  {stage}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Feedback({
  evaluation,
  showConcise = true,
}: {
  evaluation: AnswerEvaluation;
  showConcise?: boolean;
}) {
  return (
    <div className="mt-4 rounded-xl border border-divider bg-surface p-4">
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
        <p className="text-xs font-medium text-muted-foreground">Interviewer</p>
        <p className="mt-2 text-sm leading-6">{turn.question.text}</p>
      </div>
      <div className="ml-auto max-w-[88%] rounded-2xl rounded-tr-sm bg-wash px-4 py-3 text-foreground">
        <p className="text-xs font-medium opacity-70">You</p>
        <p className="mt-2 text-sm leading-6">{turn.answer_text}</p>
      </div>
      {turn.coding_answer ? (
        <details className="ml-auto max-w-[94%] rounded-xl border bg-card p-3">
          <summary className="cursor-pointer text-sm font-medium">
            Submitted Python code · {turn.coding_answer.execution.status.replace("_", " ")}
          </summary>
          <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 font-mono text-xs leading-5">
            {turn.coding_answer.code}
          </pre>
        </details>
      ) : null}
      {turn.interviewer_reaction ? (
        <div className="max-w-[88%] rounded-2xl rounded-tl-sm border border-divider bg-surface p-4">
          <p className="flex items-center gap-2 text-xs font-medium text-primary">
            <Volume2 aria-hidden className="size-3.5" />
            Interviewer response
          </p>
          <p className="mt-2 text-sm leading-6">{turn.interviewer_reaction}</p>
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
        <div className="mt-6 flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
          <div><h1 className="font-serif text-3xl font-semibold">Your interview report</h1><p className="mt-2 text-muted-foreground">{session.title.replace("Interview · ", "")} · {session.metrics.questions_answered} answered</p></div>
          <div className="rounded-xl bg-wash px-6 py-4 text-foreground"><p className="text-xs font-medium uppercase tracking-wider opacity-75">Overall</p><p className="mt-1 text-4xl font-semibold tabular-nums">{session.metrics.overall_score?.toFixed(1) ?? "—"}<span className="text-lg opacity-75">/5</span></p></div>
        </div>
        <div className="mt-6 grid gap-3 sm:grid-cols-3"><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Coverage</p><p className="mt-1 text-sm font-medium">{session.metrics.topics_covered} of {session.metrics.topics_required} topics</p></div><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Active time</p><p className="mt-1 text-sm font-medium">{clock(session.elapsed_seconds)}</p></div><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Provider cost</p><p className="mt-1 text-sm font-medium">${session.total_cost_usd.toFixed(4)} · ≈₹{(session.total_cost_usd * INR_PER_USD_ESTIMATE).toFixed(1)}</p><p className="mt-1 text-xs text-muted-foreground">Includes ${session.voice_cost_usd.toFixed(4)} voice</p></div></div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[0.8fr_1.2fr]">
        <Panel><PanelHeader><PanelTitle>Score profile</PanelTitle></PanelHeader><PanelContent className="space-y-4">{dimensions.map(([name, score]) => <div key={name}><div className="mb-2 flex justify-between gap-3 text-sm"><span>{titleCase(name)}</span><span className="font-medium tabular-nums">{score.toFixed(1)}</span></div><div className="h-2 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full", scoreColor(score))} style={{ width: `${score * 20}%` }} /></div></div>)}</PanelContent></Panel>
        <Panel><PanelHeader><PanelTitle>What to do next</PanelTitle></PanelHeader><PanelContent><p className="text-sm leading-6 text-muted-foreground">{report.evidence_confidence}</p>{session.metrics.strengths.length ? <div className="mt-4"><p className="text-sm font-medium">Strengths</p><ul className="mt-2 space-y-2 text-sm">{session.metrics.strengths.map((item) => <li key={item}>• {item}</li>)}</ul></div> : null}{report.suggested_next_steps.length ? <div className="mt-4"><p className="text-sm font-medium">Focused revision</p><ul className="mt-2 space-y-2 text-sm">{report.suggested_next_steps.map((item) => <li key={item}>• {item}</li>)}</ul></div> : null}</PanelContent></Panel>
      </div>

      <div className="space-y-4"><h2 className="text-xl font-semibold">Question review</h2>{session.turns.filter((turn) => turn.answer_text).map((turn) => <Panel key={turn.turn_index}><PanelContent className="p-6 sm:p-6"><div className="flex items-start gap-3"><span className="grid size-7 shrink-0 place-items-center rounded-full bg-wash text-xs font-semibold text-primary">{turn.turn_index + 1}</span><div className="min-w-0 flex-1"><p className="font-medium leading-6">{turn.question.text}</p><p className="mt-3 rounded-lg bg-surface p-3 text-sm leading-6">{turn.answer_text}</p>{turn.evaluation ? <><Feedback evaluation={turn.evaluation} /><div className="mt-4"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Recommended answer</p><p className="mt-2 text-sm leading-6">{turn.evaluation.recommended_answer}</p></div></> : null}<div className="mt-4 flex flex-wrap gap-2">{turn.citations.map((citation) => citation.page && session.book_id ? <Link key={citation.marker} href={`/?book=${session.book_id}&page=${citation.page}`} className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-accent">{citation.marker} · p. {citation.page}<ExternalLink aria-hidden className="size-3" /></Link> : citation.start_ms !== null && session.video_id ? <Link key={citation.marker} href={`/videos/${session.video_id}?t=${Math.floor(citation.start_ms / 1000)}`} className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-accent">{citation.marker} · {clock(citation.start_ms / 1000)}<ExternalLink aria-hidden className="size-3" /></Link> : null)}{turn.web_sources.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-accent">Web {source.rank}<ExternalLink aria-hidden className="size-3" /></a>)}</div></div></div></PanelContent></Panel>)}</div>
      {session.turns.some((turn) => turn.coding_answer) ? (
        <div className="space-y-4">
          <h2 className="text-xl font-semibold">Submitted code</h2>
          {session.turns.filter((turn) => turn.coding_answer).map((turn) => (
            <Panel key={`code-${turn.turn_index}`}>
              <PanelHeader className="pb-3">
                <PanelTitle className="text-base">Question {turn.turn_index + 1} · Python</PanelTitle>
              </PanelHeader>
              <PanelContent>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Badge variant={turn.coding_answer?.execution.status === "passed" ? "default" : "secondary"}>
                    {turn.coding_answer?.execution.status.replace("_", " ")}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {turn.hints_used} {turn.hints_used === 1 ? "hint" : "hints"} used
                  </span>
                </div>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-4 font-mono text-xs leading-5">
                  {turn.coding_answer?.code}
                </pre>
                {turn.coding_answer?.scratch_tests ? (
                  <details className="mt-3 rounded-lg border p-3">
                    <summary className="cursor-pointer text-sm font-medium">Candidate scratch tests</summary>
                    <pre className="mt-3 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 font-mono text-xs leading-5">
                      {turn.coding_answer.scratch_tests}
                    </pre>
                  </details>
                ) : null}
              </PanelContent>
            </Panel>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function InterviewWorkspace() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { session: authSession, sessionLoading } = useSession();
  const [interview, setInterview] = useState<InterviewSession | null>(null);
  const [report, setReport] = useState<InterviewReport | null>(null);
  const [answer, setAnswer] = useState("");
  const [codingDraft, setCodingDraft] = useState<CodingDraft | null>(null);
  const [transcriptCorrected, setTranscriptCorrected] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [operation, setOperation] = useState<InterviewOperation>("idle");
  const [activityStartedAt, setActivityStartedAt] = useState(Date.now());
  const [listeningPaused, setListeningPaused] = useState(false);
  const [transitionTurnIndex, setTransitionTurnIndex] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [clarificationOpen, setClarificationOpen] = useState(false);
  const [clarificationQuestion, setClarificationQuestion] = useState("");
  const [clarificationError, setClarificationError] = useState("");
  const [dictationTarget, setDictationTargetState] = useState<
    "answer" | "clarification"
  >("answer");
  const loadedAtRef = useRef(Date.now());
  const lastSpokenRef = useRef<number | null>(null);
  const draftEpochRef = useRef(0);
  const dictationTargetRef = useRef<"answer" | "clarification">("answer");
  const submissionControllerRef = useRef<AbortController | null>(null);
  const submissionInFlightRef = useRef(false);
  const legacySpeech = useInterviewerSpeech();
  const appendLiveTranscript = useCallback((text: string) => {
    if (dictationTargetRef.current === "clarification") {
      setClarificationQuestion((draft) => appendTranscriptSegment(draft, text));
    } else {
      setAnswer((draft) => appendTranscriptSegment(draft, text));
    }
  }, []);
  const livekit = useLivekitInterview({
    enabled: LIVEKIT_INTERVIEWS,
    sessionId,
    turnIndex: interview ? pendingTurn(interview)?.turn_index ?? 0 : 0,
    onTranscript: appendLiveTranscript,
  });
  const speech = LIVEKIT_INTERVIEWS ? livekit.speech : legacySpeech;
  useEffect(() => {
    if (LIVEKIT_INTERVIEWS) releasePrimedInterviewAudio();
  }, []);
  const screen = useScreenShare();

  const pending = interview ? pendingTurn(interview) : null;
  const current = transitionTurnIndex === null ? pending : null;
  const codingExercise = current?.question.coding_exercise ?? null;
  const activeCodingKey = current && codingExercise
    ? codingDraftKey(sessionId, current.turn_index)
    : null;
  const activeCodingDraft = activeCodingKey && codingDraft?.key === activeCodingKey
    ? codingDraft
    : null;
  const busy = operation !== "idle";
  const submissionLocked = ["submitting_answer", "checking_submission"].includes(operation);
  const screenBusy = operation === "screen_checkpoint";

  const setDictationTarget = useCallback(
    (target: "answer" | "clarification") => {
      if (LIVEKIT_INTERVIEWS && target !== dictationTargetRef.current) livekit.voice.stop();
      dictationTargetRef.current = target;
      setDictationTargetState(target);
    },
    [livekit.voice.stop],
  );

  useEffect(() => {
    if (
      current &&
      (current.question.work_sample === "none" || current.question.coding_exercise) &&
      screen.sharing
    ) {
      screen.stop();
    }
  }, [current, screen.sharing, screen.stop]);

  const beginOperation = useCallback((next: InterviewOperation) => {
    setOperation(next);
  }, []);
  const endOperation = useCallback((expected?: InterviewOperation) => {
    setOperation((active) => (!expected || active === expected ? "idle" : active));
  }, []);

  const acceptSubmittedAnswer = useCallback(async (
    updated: InterviewSession,
    answeredTurnIndex: number,
  ) => {
    const settledTurn = updated.turns.find(
      (turn) => turn.turn_index === answeredTurnIndex,
    );
    const reaction = settledTurn?.interviewer_reaction?.trim() ?? "";
    loadedAtRef.current = Date.now();
    if (reaction) setTransitionTurnIndex(answeredTurnIndex);
    setInterview(updated);
    window.localStorage.removeItem(codingDraftKey(sessionId, answeredTurnIndex));
    setCodingDraft(null);
    setAnswer("");
    setTranscriptCorrected(false);
    setSubmitError("");
    endOperation();
    if (reaction) {
      try {
        await speech.speakReaction(sessionId, answeredTurnIndex, reaction);
      } catch {
        setError("Your answer was saved, but spoken feedback could not be played.");
      } finally {
        setTransitionTurnIndex(null);
      }
    }
    if (["completed", "abandoned"].includes(updated.status)) {
      beginOperation("loading_report");
      try {
        setReport(await apiFetch<InterviewReport>(`/interviews/${sessionId}/report`));
      } catch (failure) {
        setError((failure as Error).message || "Your answer was saved, but the report could not be loaded.");
      }
    }
  }, [beginOperation, endOperation, sessionId, speech.speakReaction]);

  const submitAnswer = useCallback(async (text: string, corrected: boolean) => {
    const value = text.trim();
    if (
      !value ||
      !current ||
      !interview ||
      interview.status !== "active" ||
      submissionLocked ||
      (codingExercise && (!activeCodingDraft || !activeCodingDraft.code.trim())) ||
      submissionInFlightRef.current
    ) return;
    // React state does not disable the button until the next render. This ref
    // closes that same-frame window so a double click cannot start two costly
    // evaluations for the same pending turn.
    submissionInFlightRef.current = true;
    const answeredTurnIndex = current.turn_index;
    // Everything visible at this instant is the submitted answer. A Whisper
    // request already in flight belongs to the old draft and must not append
    // after this turn advances.
    draftEpochRef.current += 1;
    beginOperation("submitting_answer");
    setError("");
    setSubmitError("");
    const controller = new AbortController();
    submissionControllerRef.current = controller;
    const timeout = window.setTimeout(
      () => controller.abort(new DOMException("Answer submission timed out", "AbortError")),
      ANSWER_SUBMISSION_TIMEOUT_MS,
    );
    try {
      const codingAnswer = codingExercise && activeCodingDraft
        ? {
            language: "python" as const,
            code: activeCodingDraft.code,
            scratch_tests: activeCodingDraft.scratchTests,
            execution: activeCodingDraft.execution,
          }
        : null;
      const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/answers`, {
        method: "POST",
        body: JSON.stringify({
          answer_text: value,
          transcript_corrected: corrected,
          coding_answer: codingAnswer,
          expected_turn_index: answeredTurnIndex,
        }),
        signal: controller.signal,
      });
      await acceptSubmittedAnswer(updated, answeredTurnIndex);
    } catch (failure) {
      // A proxy or browser can lose the response after the API has committed
      // the turn. Reload before telling the candidate to resend, otherwise a
      // successful answer can look lost and be submitted twice.
      beginOperation("checking_submission");
      try {
        const uncertainOutcome =
          (failure as Error)?.name === "AbortError" || !(failure instanceof ApiError);
        let recovered: InterviewSession | null = null;
        let saved = false;
        for (const delay of uncertainOutcome
          ? SUBMISSION_RECONCILIATION_DELAYS_MS
          : [0]) {
          if (delay) await wait(delay);
          recovered = await apiFetch<InterviewSession>(`/interviews/${sessionId}`);
          saved = Boolean(
            recovered.turns.find(
              (turn) => turn.turn_index === answeredTurnIndex,
            )?.answer_text,
          );
          if (saved) break;
        }
        if (saved && recovered) {
          await acceptSubmittedAnswer(recovered, answeredTurnIndex);
        } else {
          const reason = (failure as Error)?.name === "AbortError"
            ? "The evaluator exceeded its deadline."
            : (failure as Error).message || "The evaluator did not finish.";
          setSubmitError(`Your answer was not submitted. Your draft is preserved. ${reason}`);
        }
      } catch {
        setSubmitError(
          "The app could not confirm whether the answer was saved. Your draft is preserved; check your connection before retrying.",
        );
      }
    } finally {
      window.clearTimeout(timeout);
      if (submissionControllerRef.current === controller) {
        submissionControllerRef.current = null;
      }
      submissionInFlightRef.current = false;
      endOperation();
    }
  }, [acceptSubmittedAnswer, activeCodingDraft, beginOperation, codingExercise, current, endOperation, interview, sessionId, submissionLocked]);

  const revealCodingHint = useCallback(async () => {
    if (!current || !codingExercise || interview?.feedback_mode !== "guided") return;
    beginOperation("revealing_coding_hint");
    setError("");
    try {
      setInterview(await apiFetch<InterviewSession>(
        `/interviews/${sessionId}/coding-hints`,
        { method: "POST" },
      ));
    } catch (failure) {
      setError((failure as Error).message || "The next coding hint could not be revealed.");
    } finally {
      endOperation("revealing_coding_hint");
    }
  }, [beginOperation, codingExercise, current, endOperation, interview?.feedback_mode, sessionId]);

  const handleRecording = useCallback(async (recording: Blob) => {
    const draftEpoch = draftEpochRef.current;
    const controller = new AbortController();
    const timeout = window.setTimeout(
      () => controller.abort(new DOMException("Transcription timed out", "AbortError")),
      TRANSCRIPTION_TIMEOUT_MS,
    );
    let transcript = "";
    try {
      transcript = await transcribeInterviewRecording(
        recording,
        sessionId,
        controller.signal,
      );
    } finally {
      window.clearTimeout(timeout);
    }
    // Silence and low-information noise are expected while listening remains
    // automatic. The API returns an empty transcript for those segments.
    if (!transcript.trim() || draftEpoch !== draftEpochRef.current) return;
    if (dictationTargetRef.current === "clarification") {
      setClarificationQuestion((currentQuestion) =>
        appendTranscriptSegment(currentQuestion, transcript),
      );
    } else {
      setAnswer((currentAnswer) => appendTranscriptSegment(currentAnswer, transcript));
    }
  }, [sessionId]);

  const legacyVoice = useInterviewVoice({ onRecording: handleRecording, onVoiceStart: speech.stop });
  const voice = LIVEKIT_INTERVIEWS ? livekit.voice : legacyVoice;

  const askClarification = useCallback(async () => {
    const value = clarificationQuestion.trim();
    if (!value || !current || !interview || interview.status !== "active") return;
    const clarificationIndex = current.question.clarifications.length;
    voice.stop();
    speech.stop();
    setListeningPaused(true);
    setClarificationError("");
    beginOperation("asking_clarification");
    try {
      const updated = await apiFetch<InterviewSession>(
        `/interviews/${sessionId}/clarifications`,
        {
          method: "POST",
          body: JSON.stringify({ question: value }),
        },
      );
      setInterview(updated);
      setClarificationQuestion("");
      setClarificationOpen(false);
      setDictationTarget("answer");
      endOperation("asking_clarification");
      const updatedTurn = updated.turns.find(
        (turn) => turn.turn_index === current.turn_index,
      );
      const response = updatedTurn?.question.clarifications.at(-1)?.interviewer_response;
      if (response) {
        await speech.speakClarification(
          sessionId,
          current.turn_index,
          clarificationIndex,
          response,
        );
      }
      setListeningPaused(false);
    } catch (failure) {
      setClarificationError(
        (failure as Error).message || "The interviewer could not clarify that question.",
      );
      setListeningPaused(false);
    } finally {
      endOperation("asking_clarification");
    }
  }, [
    beginOperation,
    clarificationQuestion,
    current,
    endOperation,
    interview,
    sessionId,
    speech.speakClarification,
    speech.stop,
    setDictationTarget,
    voice.stop,
  ]);

  const load = useCallback(async () => {
    beginOperation("loading_session");
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
    } finally {
      endOperation();
    }
  }, [beginOperation, endOperation, sessionId]);

  useEffect(() => { if (authSession) void load(); }, [authSession, load]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(timer); }, []);
  useEffect(() => {
    setClarificationOpen(false);
    setClarificationQuestion("");
    setClarificationError("");
    setDictationTarget("answer");
  }, [current?.turn_index, setDictationTarget]);

  useEffect(() => {
    if (!activeCodingKey || !codingExercise) {
      setCodingDraft(null);
      return;
    }
    let restored: (Partial<CodingDraft> & { explanation?: string }) = {};
    try {
      restored = JSON.parse(
        window.localStorage.getItem(activeCodingKey) ?? "{}",
      ) as Partial<CodingDraft> & { explanation?: string };
    } catch {
      window.localStorage.removeItem(activeCodingKey);
    }
    setCodingDraft({
      key: activeCodingKey,
      code: restored.code || codingExercise.starter_code,
      scratchTests: restored.scratchTests || "",
      execution: restored.execution || emptyPythonExecution(),
    });
    if (typeof restored.explanation === "string") {
      setAnswer(restored.explanation);
    }
  }, [activeCodingKey, codingExercise?.starter_code]);

  useEffect(() => {
    if (!activeCodingKey || !activeCodingDraft) return;
    window.localStorage.setItem(
      activeCodingKey,
      JSON.stringify({ ...activeCodingDraft, explanation: answer }),
    );
  }, [activeCodingDraft, activeCodingKey, answer]);

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
    void speech.speak(
      sessionId,
      current.turn_index,
      spokenInterviewQuestion(current.question),
    );
  }, [current, interview?.status, sessionId, speech.speak]);

  const elapsed = useMemo(() => {
    if (!interview) return 0;
    return interview.elapsed_seconds + (interview.status === "active" ? Math.floor((now - loadedAtRef.current) / 1000) : 0);
  }, [interview, now]);
  const remaining = Math.max(0, (interview?.maximum_duration_minutes ?? 0) * 60 - elapsed);
  const coverage = interview?.metrics.topics_required ? (interview.metrics.topics_covered / interview.metrics.topics_required) * 100 : interview?.checkpoint.topics.length ? (interview.checkpoint.topics.filter((topic) => topic.required && topic.completed).length / interview.checkpoint.topics.filter((topic) => topic.required).length) * 100 : 0;
  const activity = useMemo(
    () => describeInterviewActivity({
      operation,
      interviewStatus: interview?.status ?? "ready",
      transitionInProgress: transitionTurnIndex !== null,
      speechLoading: speech.loading,
      speechSpeaking: speech.speaking,
      voiceStatus: voice.status,
      dictationTarget,
      listeningPaused,
      hasCurrentQuestion: current !== null,
    }),
    [current, dictationTarget, interview?.status, listeningPaused, operation, speech.loading, speech.speaking, transitionTurnIndex, voice.status],
  );
  useEffect(() => setActivityStartedAt(Date.now()), [activity.title]);
  const operationElapsed = activity.tone !== "working"
    ? null
    : Math.max(0, Math.floor((now - activityStartedAt) / 1_000));

  const pause = useCallback(async () => {
    voice.stop(); speech.stop(); beginOperation("pausing");
    try { const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/pause`, { method: "POST" }); loadedAtRef.current = Date.now(); setInterview(updated); }
    catch (failure) { setError((failure as Error).message); }
    finally { endOperation(); }
  }, [beginOperation, endOperation, sessionId, speech.stop, voice.stop]);
  const resume = useCallback(async () => {
    if (!LIVEKIT_INTERVIEWS) primeInterviewAudio();
    primeInterviewerSpeech();
    lastSpokenRef.current = null;
    setListeningPaused(false);
    beginOperation("resuming");
    try { const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/resume`, { method: "POST" }); loadedAtRef.current = Date.now(); setInterview(updated); }
    catch (failure) { releasePrimedInterviewAudio(); setError((failure as Error).message); }
    finally { endOperation(); }
  }, [beginOperation, endOperation, sessionId]);
  const finish = useCallback(async () => {
    voice.stop(); speech.stop(); beginOperation("finishing");
    try { const updated = await apiFetch<InterviewSession>(`/interviews/${sessionId}/finish`, { method: "POST" }); setInterview(updated); beginOperation("loading_report"); setReport(await apiFetch<InterviewReport>(`/interviews/${sessionId}/report`)); }
    catch (failure) { setError((failure as Error).message); }
    finally { endOperation(); }
  }, [beginOperation, endOperation, sessionId, speech.stop, voice.stop]);

  useEffect(() => {
    if (!interview || interview.status !== "active" || busy) return;
    const ceiling = interview.maximum_duration_minutes * 60;
    if (elapsed >= ceiling + 120) void finish();
  }, [busy, elapsed, finish, interview]);

  const submitScreen = useCallback(async () => {
    const draftEpoch = draftEpochRef.current;
    beginOperation("screen_checkpoint"); setError("");
    try {
      const blob = await screen.capture();
      const token = await accessToken();
      const response = await fetch(uploadUrl(`/interviews/${sessionId}/screen-checkpoints`), { method: "POST", headers: { "Content-Type": blob.type, ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: blob });
      if (!response.ok) throw new ApiError(await errorDetail(response), response.status);
      const updated = (await response.json()) as InterviewSession;
      if (draftEpoch === draftEpochRef.current) setInterview(updated);
    } catch (failure) {
      if (draftEpoch === draftEpochRef.current) {
        setError((failure as Error).message || "Screen checkpoint failed.");
      }
    }
    finally { endOperation("screen_checkpoint"); }
  }, [beginOperation, endOperation, screen, sessionId]);

  if (sessionLoading || (authSession && !interview && !error)) return <div className="grid h-dvh place-items-center p-6"><div className="flex max-w-sm items-start gap-3 rounded-xl border bg-card p-6" role="status" aria-live="polite"><Loader2 aria-hidden className="mt-1 size-5 shrink-0 animate-spin text-primary motion-reduce:animate-none" /><div><p className="font-medium">{sessionLoading ? "Checking your session" : "Restoring your interview"}</p><p className="mt-1 text-sm leading-6 text-muted-foreground">{sessionLoading ? "Verifying sign-in before loading interview data." : "Loading the saved question, transcript, timer, and media state."}</p></div></div></div>;
  if (!authSession) return <div className="relative grid h-dvh place-items-center p-6"><div className="absolute right-3 top-3"><ThemeToggle /></div><AuthGate /></div>;
  if (!interview) return <div className="grid h-dvh place-items-center p-6"><Alert variant="destructive" className="max-w-lg"><AlertDescription>{error || "Interview not found."}</AlertDescription></Alert></div>;

  return (
    <AppShell
      section="interviews"
      status={<span>{interview.status === "active" ? (remaining > 0 ? `${clock(remaining)} remaining` : "Finish your current answer") : titleCase(interview.status)}</span>}
      account={<AccountMenu email={authSession.user.email} />}
      rail={<div className="flex h-full flex-col overflow-y-auto p-4">
<Link href="/interviews" className="mb-6 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft aria-hidden className="size-4" />All interviews</Link><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Scope</p><h2 className="mt-2 font-serif text-base font-medium">{interview.title.replace("Interview · ", "")}</h2><p className="mt-1 text-xs text-muted-foreground">{interview.source_title}</p><div className="mt-6 space-y-3 border-t pt-4"><div className="flex justify-between text-xs"><span className="text-muted-foreground">Format</span><span className="capitalize">{interview.interview_format.replace("_", " ")}</span></div><div className="flex justify-between text-xs"><span className="text-muted-foreground">Level</span><span className="capitalize">{interview.target_level}</span></div><div className="flex justify-between text-xs"><span className="text-muted-foreground">Mode</span><span className="capitalize">{interview.feedback_mode}</span></div></div><div className="mt-6"><div className="mb-2 flex justify-between text-xs"><span>Source coverage</span><span>{Math.round(coverage)}%</span></div><Progress value={coverage} /></div><p className="mt-4 text-xs leading-5 text-muted-foreground">Topic order and future questions remain hidden. The interview finishes early when meaningful coverage is complete.</p></div>}
    >
      {report ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <SessionReportView report={report} />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
            {error || voice.error || speech.error || screen.error ? <Alert variant="destructive"><AlertDescription>{error || voice.error || speech.error || screen.error}</AlertDescription></Alert> : null}

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3">
              <div className="flex items-center gap-3"><div className={cn("size-2.5 rounded-full", interview.status === "active" ? "bg-positive motion-safe:animate-pulse" : "bg-warning")} /><div><p className="text-sm font-medium">{titleCase(interview.status)}</p><p className="text-xs text-muted-foreground">{clock(elapsed)} elapsed · {remaining > 0 ? `${clock(remaining)} remaining` : "current answer only"}</p></div></div>
              <div className="flex items-center gap-2">{interview.status === "active" ? <Button variant="outline" size="sm" onClick={() => void pause()} disabled={busy}>{operation === "pausing" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Pause aria-hidden />}{operation === "pausing" ? "Pausing…" : "Pause"}</Button> : <Button variant="outline" size="sm" onClick={() => void resume()} disabled={busy}>{operation === "resuming" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Play aria-hidden />}{operation === "resuming" ? "Resuming…" : "Resume"}</Button>}<Button variant="ghost" size="sm" onClick={() => void finish()} disabled={busy}>{operation === "finishing" || operation === "loading_report" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : null}{operation === "finishing" ? "Finishing…" : operation === "loading_report" ? "Loading report…" : "End interview"}</Button></div>
            </div>

            <ActivityStatus activity={activity} elapsedSeconds={operationElapsed} />

            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
              <div className="space-y-6">
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
                  <Panel className="overflow-hidden border-action shadow-sm">
                    <div className="h-1 bg-primary" />
                    <PanelHeader className="pb-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Badge variant="outline">Question {current.turn_index + 1}</Badge>
                        <Badge variant="secondary" className="capitalize">{current.question.kind.replace("_", " ")}</Badge>
                      </div>
                      <PanelTitle className="mt-3 text-xl leading-8 sm:text-2xl">{current.question.text}</PanelTitle>
                    </PanelHeader>
                    <PanelContent>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy || speech.loading}
                          onClick={() => {
                            if (speech.speaking) speech.stop();
                            else {
                              lastSpokenRef.current = current.turn_index;
                              void speech.speak(
                                sessionId,
                                current.turn_index,
                                spokenInterviewQuestion(current.question),
                              );
                            }
                          }}
                        >
                          {speech.speaking ? <VolumeX aria-hidden /> : speech.loading ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Volume2 aria-hidden />}
                          {speech.speaking ? "Stop voice" : speech.loading ? "Generating question voice…" : "Hear question again"}
                        </Button>
                        {voice.supported ? (
                          <Button
                            variant={voice.status === "idle" ? "outline" : "secondary"}
                            size="sm"
                            disabled={busy || speech.loading || speech.speaking}
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
                            {voice.status === "starting" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Mic aria-hidden />}
                            {voice.status === "starting" ? "Connecting mic…" : voice.status === "idle" ? "Start listening" : "Pause listening"}
                          </Button>
                        ) : null}
                        {current.question.clarifications.length < 4 ? (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={submissionLocked || operation === "asking_clarification"}
                            onClick={() => {
                              voice.stop();
                              speech.stop();
                              setDictationTarget("clarification");
                              setListeningPaused(false);
                              setClarificationOpen(true);
                              setClarificationError("");
                            }}
                          >
                            <MessageCircleQuestion aria-hidden />
                            Ask a clarifying question
                          </Button>
                        ) : null}
                      </div>
                      {current.question.clarifications.length ? (
                        <div className="mt-4 space-y-3" aria-label="Question clarifications">
                          {current.question.clarifications.map((item, index) => (
                            <div key={`${index}-${item.candidate_question}`} className="rounded-lg border bg-surface p-3 text-sm leading-6">
                              <p><span className="font-medium">You asked:</span> {item.candidate_question}</p>
                              <p className="mt-1 text-muted-foreground"><span className="font-medium text-foreground">Interviewer:</span> {item.interviewer_response}</p>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {clarificationOpen ? (
                        <div className="mt-4 rounded-xl border border-divider bg-surface p-4">
                          <label htmlFor="candidate-clarification" className="text-sm font-medium">
                            What should the interviewer clarify?
                          </label>
                          <p className="mt-1 text-xs leading-5 text-muted-foreground">
                            Speak or type here. This is sent separately and will not be added to your answer.
                          </p>
                          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-divider bg-canvas px-3 py-2" role="status" aria-live="polite">
                            <span className="text-xs font-medium text-primary">
                              Voice target: clarifying question
                            </span>
                            {voice.status === "recording" ? (
                              <Button type="button" size="sm" variant="outline" onClick={voice.finishSegment}>
                                <Square aria-hidden />
                                Transcribe now
                              </Button>
                            ) : (
                              <span className="text-xs text-muted-foreground">
                                {voice.status === "processing"
                                  ? "Transcribing into this box…"
                                  : voice.status === "listening"
                                    ? "Listening for this clarification…"
                                    : "Microphone is paused"}
                              </span>
                            )}
                          </div>
                          <Textarea
                            id="candidate-clarification"
                            value={clarificationQuestion}
                            onChange={(event) => setClarificationQuestion(event.target.value)}
                            placeholder="For example: Which equation should I derive, and what does each variable represent?"
                            className="mt-2 min-h-20 resize-y"
                            maxLength={1000}
                            autoFocus
                            disabled={operation === "asking_clarification"}
                          />
                          {clarificationError ? (
                            <p className="mt-2 text-sm text-destructive" role="alert">{clarificationError}</p>
                          ) : null}
                          <div className="mt-3 flex flex-wrap justify-end gap-2">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={operation === "asking_clarification"}
                              onClick={() => {
                                setClarificationOpen(false);
                                setClarificationQuestion("");
                                setClarificationError("");
                                setDictationTarget("answer");
                                setListeningPaused(false);
                              }}
                            >
                              Cancel
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              disabled={!clarificationQuestion.trim() || operation === "asking_clarification"}
                              onClick={() => void askClarification()}
                            >
                              {operation === "asking_clarification" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <MessageCircleQuestion aria-hidden />}
                              {operation === "asking_clarification" ? "Clarifying…" : "Ask interviewer"}
                            </Button>
                          </div>
                        </div>
                      ) : null}
                      {current.question.work_sample !== "none" &&
                      !current.question.coding_exercise &&
                      current.question.work_sample_prompt ? (
                        <div className="mt-4 rounded-xl border border-divider bg-surface p-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <p className="flex items-center gap-2 text-sm font-semibold text-primary">
                                <MonitorUp aria-hidden className="size-4" />
                                {workSampleLabel(current.question.work_sample)}
                              </p>
                              <p className="mt-2 text-sm leading-6">
                                {current.question.work_sample_prompt}
                              </p>
                              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                                The interviewer requested this automatically. Your browser requires you to choose the shared window; only a still checkpoint is uploaded when you submit it.
                              </p>
                            </div>
                            {!screen.sharing ? (
                              <Button
                                size="sm"
                                disabled={!screen.supported || interview.status !== "active" || busy}
                                onClick={() => void screen.start()}
                              >
                                <MonitorUp aria-hidden />
                                Start screen task
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                disabled={busy}
                                onClick={() => void submitScreen()}
                              >
                                {screenBusy ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : null}
                                {screenBusy ? "Analyzing checkpoint…" : "Submit current screen"}
                              </Button>
                            )}
                          </div>
                          {!screen.supported ? (
                            <p className="mt-3 text-xs text-destructive">
                              Screen sharing is unavailable in this browser. You can still describe the work in your answer.
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                      {current.screen_observation ? <Alert className="mt-4"><MonitorUp aria-hidden /><AlertDescription>Screen checkpoint received: {current.screen_observation.summary}</AlertDescription></Alert> : null}
                    </PanelContent>
                  </Panel>
                ) : null}

                {current && codingExercise && activeCodingDraft && interview.status === "active" ? (
                  <PythonCodingWorkspace
                    exercise={codingExercise}
                    code={activeCodingDraft.code}
                    scratchTests={activeCodingDraft.scratchTests}
                    execution={activeCodingDraft.execution}
                    disabled={submissionLocked}
                    hintsUsed={current.hints_used}
                    availableHints={current.available_coding_hints ?? 0}
                    hintLoading={operation === "revealing_coding_hint"}
                    onCodeChange={(code) => setCodingDraft((draft) =>
                      draft?.key === activeCodingKey ? { ...draft, code } : draft
                    )}
                    onScratchTestsChange={(scratchTests) => setCodingDraft((draft) =>
                      draft?.key === activeCodingKey ? { ...draft, scratchTests } : draft
                    )}
                    onExecution={(execution) => setCodingDraft((draft) =>
                      draft?.key === activeCodingKey ? { ...draft, execution } : draft
                    )}
                    onRevealHint={interview.feedback_mode === "guided" ? () => void revealCodingHint() : undefined}
                  />
                ) : null}

                {current && interview.status === "active" ? (
                  <Panel id="question">
                    <PanelContent className="p-4">
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className={cn("size-2 rounded-full", voice.status === "recording" ? "bg-destructive motion-safe:animate-pulse" : voice.status === "listening" ? "bg-positive" : "bg-divider")} />
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
                                className="block h-full rounded-full bg-positive transition-[width] duration-75"
                                style={{ width: `${Math.round(voice.inputLevel * 100)}%` }}
                              />
                            </span>
                          ) : null}
                          <span className="text-xs text-muted-foreground">
                            {speech.loading
                              ? "Generating interviewer voice — listening starts after playback"
                              : speech.speaking
                                ? "Interviewer speaking — listening starts automatically next"
                              : voice.status === "recording"
                              ? dictationTarget === "clarification"
                                ? "Capturing your clarifying question…"
                                : "Capturing this part of your answer…"
                              : voice.status === "processing"
                                ? dictationTarget === "clarification"
                                  ? "Speech recognition is transcribing into the clarification box…"
                                  : "Speech recognition is transcribing this segment into your answer…"
                                : voice.status === "listening"
                                  ? voice.mode === "automatic"
                                    ? dictationTarget === "clarification"
                                      ? "Listening — speech goes only to the clarification box"
                                      : "Listening continuously — speech goes only to your answer"
                                    : "Hold the mic button to speak"
                                  : "Type your answer or start listening"}
                          </span>
                          {voice.status === "recording" && dictationTarget === "answer" ? (
                            <Button type="button" size="sm" variant="outline" onClick={voice.finishSegment}>
                              <Square aria-hidden />
                              Transcribe now
                            </Button>
                          ) : null}
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <Select
                            disabled={busy}
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
                            <Select disabled={busy} value={voice.mode} onValueChange={(value) => voice.setMode(value as "automatic" | "push_to_talk")}>
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
                        placeholder={codingExercise
                          ? "Explain your approach, complexity, and any trade-offs. Code and explanation are submitted together."
                          : "Speak or type your answer. Nothing is sent until you choose Send answer."}
                        className="min-h-32 resize-y text-base leading-6"
                        disabled={submissionLocked}
                      />
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs leading-5">
                        <span className="font-medium text-primary">
                          Voice target: {dictationTarget === "clarification" ? "clarifying question" : "answer draft"}
                        </span>
                        {dictationTarget === "clarification" ? (
                          <span className="text-muted-foreground">Your answer draft is not being changed.</span>
                        ) : null}
                      </div>
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        Thinking pauses are safe. Send is always available and submits exactly the text currently visible; unfinished speech is left out.
                      </p>
                      {operation === "submitting_answer" ? (
                        <div className="mt-3 rounded-lg border border-divider bg-surface p-3 text-xs leading-5" role="status" aria-live="polite">
                          <p className="font-medium">Evaluating and saving your answer · {operationElapsed ?? 0}s</p>
                          <p className="text-muted-foreground">
                            Comparing it with source evidence, validating feedback, and preparing one focused next question. Your draft stays here until the save is confirmed.
                          </p>
                          {(operationElapsed ?? 0) >= 15 ? (
                            <p className="mt-1 font-medium text-warning dark:text-warning">
                              The interview model is taking longer than usual. Please do not resend; recovery will check whether this turn was saved.
                            </p>
                          ) : null}
                          {(operationElapsed ?? 0) >= 40 ? (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="mt-2"
                              onClick={() => submissionControllerRef.current?.abort(
                                new DOMException("Candidate stopped waiting", "AbortError"),
                              )}
                            >
                              Stop waiting and check status
                            </Button>
                          ) : null}
                        </div>
                      ) : operation === "checking_submission" ? (
                        <div className="mt-3 rounded-lg border border-warning bg-warning-wash p-3 text-xs leading-5" role="status" aria-live="polite">
                          <p className="font-medium">Checking the saved session before enabling retry…</p>
                          <p className="text-muted-foreground">Your answer remains in the editor during this check.</p>
                        </div>
                      ) : submitError ? (
                        <Alert variant="destructive" className="mt-3">
                          <AlertDescription>{submitError}</AlertDescription>
                        </Alert>
                      ) : null}
                      <div className="mt-3 flex items-center justify-between gap-3">
                        {voice.mode === "push_to_talk" && voice.status !== "idle" ? (
                          <Button type="button" variant="secondary" onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); void voice.beginPush(); }} onPointerUp={voice.endPush} onPointerCancel={voice.endPush}
                            onKeyDown={(event) => { if ((event.key === " " || event.key === "Enter") && !event.repeat) { event.preventDefault(); void voice.beginPush(); } }}
                            onKeyUp={(event) => { if (event.key === " " || event.key === "Enter") { event.preventDefault(); voice.endPush(); } }}
                            onBlur={voice.endPush}>
                            <Mic aria-hidden />Hold to talk
                          </Button>
                        ) : <span />}
                        <Button
                          disabled={
                            !answer.trim() ||
                            Boolean(codingExercise && !activeCodingDraft?.code.trim()) ||
                            submissionLocked
                          }
                          onClick={() => {
                            voice.stop();
                            void submitAnswer(answer, transcriptCorrected);
                          }}
                        >
                          {submissionLocked
                            ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" />
                            : <Send aria-hidden />}
                          {operation === "submitting_answer"
                            ? `Evaluating answer… ${operationElapsed ?? 0}s`
                            : operation === "checking_submission"
                              ? "Confirming save…"
                              : "Send answer"}
                        </Button>
                      </div>
                    </PanelContent>
                  </Panel>
                ) : current && interview.status === "paused" ? (
                  <Panel className="border-warning bg-warning-wash">
                    <PanelContent className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="font-medium">This interview is paused</p>
                        <p className="mt-1 text-sm leading-6 text-muted-foreground">
                          Resume to replay the current question and restore automatic listening.
                        </p>
                      </div>
                      <Button onClick={() => void resume()} disabled={busy}>
                        {operation === "resuming" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Play aria-hidden />}
                        {operation === "resuming" ? "Restoring interview…" : "Resume interview"}
                      </Button>
                    </PanelContent>
                  </Panel>
                ) : null}
              </div>

              <aside className="space-y-4">
                <Panel>
                  <PanelHeader className="pb-3">
                    <PanelTitle className="flex items-center gap-2 text-base">
                      <MonitorUp aria-hidden className="size-4" />
                      {codingExercise ? "Coding workspace" : "Screen workspace"}
                    </PanelTitle>
                  </PanelHeader>
                  <PanelContent>
                    <p className="text-xs leading-5 text-muted-foreground">
                      {codingExercise
                        ? "Python runs locally in a resettable browser worker. Your source is uploaded only with the answer you explicitly submit."
                        : current && current.question.work_sample !== "none" && current.question.work_sample_prompt
                          ? `${workSampleLabel(current.question.work_sample)} requested. Share the requested diagram or derivation when it is ready. Nothing is continuously uploaded.`
                          : "No screen task for this question. Answer verbally or in the text box. Nothing is continuously uploaded."}
                    </p>
                    {!codingExercise ? (
                      <>
                        <video ref={screen.videoRef} muted playsInline className={cn("mt-3 aspect-video w-full rounded-lg border bg-black object-contain", !screen.sharing && "hidden")} />
                        {current && current.question.work_sample !== "none" ? (
                          <div className="mt-3 flex gap-2">
                            {!screen.sharing ? (
                              <Button variant="outline" size="sm" className="w-full" disabled={!screen.supported || interview.status !== "active" || busy} onClick={() => void screen.start()}><MonitorUp aria-hidden />Share screen</Button>
                            ) : (
                              <>
                                <Button size="sm" className="flex-1" disabled={busy} onClick={() => void submitScreen()}>{screenBusy ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : null}{screenBusy ? "Analyzing checkpoint…" : "Submit screen"}</Button>
                                <Button variant="outline" size="icon-sm" aria-label="Stop sharing" onClick={screen.stop}><Square aria-hidden /></Button>
                              </>
                            )}
                          </div>
                        ) : null}
                      </>
                    ) : null}
                  </PanelContent>
                </Panel>
                <Panel><PanelHeader className="pb-3"><PanelTitle className="flex items-center gap-2 text-base"><Clock3 aria-hidden className="size-4" />Session budget</PanelTitle></PanelHeader><PanelContent><div className="flex items-end justify-between"><div><p className="text-2xl font-semibold tabular-nums">${interview.total_cost_usd.toFixed(4)}</p><p className="text-xs text-muted-foreground">≈₹{(interview.total_cost_usd * INR_PER_USD_ESTIMATE).toFixed(1)} provider cost · ${interview.voice_cost_usd.toFixed(4)} voice</p></div><span className="text-xs text-muted-foreground">Target ₹5–10</span></div><Progress className="mt-3" value={Math.min(100, (interview.total_cost_usd * INR_PER_USD_ESTIMATE / 10) * 100)} /><p className="mt-3 text-xs leading-5 text-muted-foreground">Raw audio and screen images are discarded after processing. The total includes metered LiveKit STT and TTS usage at configured rates.</p></PanelContent></Panel>
              </aside>
            </div>
          </div>
        </div>
                  )}
    </AppShell>
  );
}
