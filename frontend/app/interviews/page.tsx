"use client";

import {
  ArrowRight,
  Clock3,
  LogOut,
  MessagesSquare,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { MicrophoneSetup } from "@/components/interviews/microphone-setup";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { primeInterviewerSpeech } from "@/hooks/use-interviewer-speech";
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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { signOut, useSession } from "@/hooks/use-session";
import {
  primeInterviewAudio,
  releasePrimedInterviewAudio,
} from "@/hooks/use-interview-voice";
import { apiFetch } from "@/lib/api";
import type { ChapterListResponse, ChapterSummary } from "@/lib/deck-types";
import {
  formatDuration,
  INTERVIEW_DURATIONS,
  type InterviewFormatChoice,
  type InterviewMode,
  type InterviewPreflight,
  type InterviewSession,
  type InterviewSourceKind,
  type TargetLevel,
} from "@/lib/interview-types";
import type { BookListResponse, BookSummary } from "@/lib/types";
import type { VideoListResponse, VideoSummary } from "@/lib/video-types";
import { videoState } from "@/lib/video-state";
import { cn } from "@/lib/utils";

type SetupPayload = {
  source_kind: InterviewSourceKind;
  book_id?: number;
  node_id?: number;
  video_id?: string;
  maximum_duration_minutes: number;
  target_level: TargetLevel;
  feedback_mode: InterviewMode;
  interview_format: InterviewFormatChoice;
};

const LEVELS: Array<{ value: TargetLevel; label: string; note: string }> = [
  { value: "entry", label: "Entry", note: "Foundations and clear explanations" },
  { value: "mid", label: "Mid-level", note: "Depth, applications, and trade-offs" },
  { value: "senior", label: "Senior", note: "Judgment, failure modes, and ambiguity" },
];

function statusLabel(status: InterviewSession["status"]): string {
  if (status === "active") return "In progress";
  if (status === "paused") return "Paused";
  if (status === "completed") return "Complete";
  if (status === "abandoned") return "Ended";
  return "Ready";
}

export default function InterviewsPage() {
  const router = useRouter();
  const { session, sessionLoading } = useSession();
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [history, setHistory] = useState<InterviewSession[]>([]);
  const [sourceKind, setSourceKind] = useState<InterviewSourceKind>("book");
  const [bookId, setBookId] = useState("");
  const [nodeId, setNodeId] = useState("");
  const [videoId, setVideoId] = useState("");
  const [duration, setDuration] = useState(30);
  const [level, setLevel] = useState<TargetLevel>("mid");
  const [mode, setMode] = useState<InterviewMode>("realistic");
  const [format, setFormat] = useState<InterviewFormatChoice>("auto");
  const [preview, setPreview] = useState<InterviewPreflight | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [microphoneReady, setMicrophoneReady] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [bookPayload, videoPayload, interviewPayload] = await Promise.all([
        apiFetch<BookListResponse>("/books"),
        apiFetch<VideoListResponse>("/videos"),
        apiFetch<{ sessions: InterviewSession[] }>("/interviews"),
      ]);
      setBooks(bookPayload.books);
      setVideos(
        videoPayload.videos.filter((video) =>
          ["ready", "partial"].includes(videoState(video)),
        ),
      );
      setHistory(interviewPayload.sessions);
      setError("");
    } catch (failure) {
      setError((failure as Error).message || "Could not load interview setup.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  useEffect(() => {
    setPreview(null);
    if (!bookId) {
      setChapters([]);
      setNodeId("");
      return;
    }
    setNodeId("");
    void apiFetch<ChapterListResponse>(`/books/${bookId}/chapters`)
      .then((payload) => setChapters(payload.chapters))
      .catch((failure) =>
        setError((failure as Error).message || "Could not load chapters."),
      );
  }, [bookId]);

  useEffect(() => setPreview(null), [sourceKind, nodeId, videoId, duration, level, mode, format]);

  const payload = useMemo<SetupPayload | null>(() => {
    if (sourceKind === "book" && (!bookId || !nodeId)) return null;
    if (sourceKind === "video" && !videoId) return null;
    return {
      source_kind: sourceKind,
      ...(sourceKind === "book"
        ? { book_id: Number(bookId), node_id: Number(nodeId) }
        : { video_id: videoId }),
      maximum_duration_minutes: duration,
      target_level: level,
      feedback_mode: mode,
      interview_format: format,
    };
  }, [bookId, duration, format, level, mode, nodeId, sourceKind, videoId]);

  const review = useCallback(async () => {
    if (!payload) return;
    setBusy(true);
    setError("");
    try {
      setPreview(
        await apiFetch<InterviewPreflight>("/interviews/preflight", {
          method: "POST",
          body: JSON.stringify(payload),
        }),
      );
    } catch (failure) {
      setError((failure as Error).message || "Could not inspect this source.");
    } finally {
      setBusy(false);
    }
  }, [payload]);

  const begin = useCallback(async () => {
    if (!payload) return;
    primeInterviewAudio();
    primeInterviewerSpeech();
    setBusy(true);
    setError("");
    try {
      const created = await apiFetch<InterviewSession>("/interviews", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await apiFetch<InterviewSession>(`/interviews/${created.session_id}/start`, {
        method: "POST",
      });
      router.push(`/interviews/${created.session_id}`);
    } catch (failure) {
      releasePrimedInterviewAudio();
      setError((failure as Error).message || "Could not start the interview.");
      setBusy(false);
    }
  }, [payload, router]);

  if (sessionLoading) {
    return <div className="grid h-dvh place-items-center"><Skeleton className="h-6 w-52" /></div>;
  }
  if (!session) {
    return (
      <div className="relative grid h-dvh place-items-center p-6">
        <div className="absolute right-3 top-3"><ThemeToggle /></div>
        <AuthGate />
      </div>
    );
  }

  return (
    <AppShell
      nav={<SectionNav active="interviews" />}
      status={<span>Source-grounded interview practice</span>}
      account={
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="max-w-44">
              <span className="truncate">{session.user.email}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="font-normal text-muted-foreground">Signed in</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => signOut()}><LogOut aria-hidden />Sign out</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      }
      rail={
        <div className="flex h-full flex-col overflow-y-auto p-4">
          <div className="mb-5 sm:hidden"><SectionNav active="interviews" /></div>
          <h2 className="font-heading text-sm font-medium">Recent interviews</h2>
          <p className="mt-1 text-xs text-muted-foreground">Resume a paused session or revisit a report.</p>
          <div className="mt-4 space-y-2">
            {history.length === 0 ? (
              <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">Your sessions will appear here.</p>
            ) : history.map((item) => (
              <Link
                key={item.session_id}
                href={`/interviews/${item.session_id}`}
                className="block rounded-lg border border-transparent px-3 py-2.5 transition-colors hover:border-border hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="line-clamp-2 text-sm font-medium">{item.title.replace("Interview · ", "")}</p>
                  <Badge variant="outline" className="shrink-0 text-[10px]">{statusLabel(item.status)}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{item.target_level} · {formatDuration(item.maximum_duration_minutes)} max</p>
              </Link>
            ))}
          </div>
        </div>
      }
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-8 sm:py-12">
          <div className="max-w-2xl">
            <Badge variant="secondary" className="mb-4"><Sparkles aria-hidden /> Adaptive practice</Badge>
            <h1 className="font-heading text-3xl font-semibold tracking-tight sm:text-4xl">Practice the interview, not a question list.</h1>
            <p className="mt-3 text-base leading-7 text-muted-foreground">Choose one chapter or lecture. The interviewer follows its evidence, adapts to your answers, and finishes when the useful material is covered.</p>
          </div>

          {error ? <Alert variant="destructive" className="mt-6"><AlertDescription>{error}</AlertDescription></Alert> : null}

          <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_0.78fr]">
            <Card>
              <CardHeader><CardTitle>Interview setup</CardTitle></CardHeader>
              <CardContent className="space-y-6">
                <fieldset className="space-y-3">
                  <legend className="text-sm font-medium">Study source</legend>
                  <div className="grid grid-cols-2 gap-2 rounded-lg bg-muted p-1">
                    {(["book", "video"] as InterviewSourceKind[]).map((value) => (
                      <button key={value} type="button" aria-pressed={sourceKind === value} onClick={() => setSourceKind(value)} className={cn("rounded-md px-3 py-2 text-sm transition-colors", sourceKind === value ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                        {value === "book" ? "Book chapter" : "Lecture"}
                      </button>
                    ))}
                  </div>
                  {sourceKind === "book" ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="space-y-1.5"><Label htmlFor="interview-book">Book</Label><Select value={bookId} onValueChange={setBookId}><SelectTrigger id="interview-book"><SelectValue placeholder={loaded ? "Choose a book" : "Loading…"} /></SelectTrigger><SelectContent>{books.map((book) => <SelectItem key={book.book_id} value={String(book.book_id)}>{book.title}</SelectItem>)}</SelectContent></Select></div>
                      <div className="space-y-1.5"><Label htmlFor="interview-chapter">Chapter</Label><Select value={nodeId} onValueChange={setNodeId} disabled={!bookId || chapters.length === 0}><SelectTrigger id="interview-chapter"><SelectValue placeholder={bookId ? "Choose a chapter" : "Choose a book first"} /></SelectTrigger><SelectContent>{chapters.map((chapter) => <SelectItem key={chapter.node_id} value={String(chapter.node_id)}>{chapter.title}</SelectItem>)}</SelectContent></Select></div>
                    </div>
                  ) : (
                    <div className="space-y-1.5"><Label htmlFor="interview-video">Lecture</Label><Select value={videoId} onValueChange={setVideoId}><SelectTrigger id="interview-video"><SelectValue placeholder="Choose a processed lecture" /></SelectTrigger><SelectContent>{videos.map((video) => <SelectItem key={video.video_id} value={video.video_id}>{video.title}</SelectItem>)}</SelectContent></Select></div>
                  )}
                </fieldset>

                <fieldset className="space-y-3"><legend className="text-sm font-medium">Maximum time</legend><div className="grid grid-cols-3 gap-2 sm:grid-cols-6">{INTERVIEW_DURATIONS.map((value) => <button key={value} type="button" onClick={() => setDuration(value)} aria-pressed={duration === value} className={cn("rounded-md border px-2 py-2 text-sm transition-colors", duration === value ? "border-primary bg-primary text-primary-foreground" : "border-border hover:bg-accent")}>{value < 60 ? value : value === 60 ? "1h" : value === 90 ? "1.5h" : "2h"}</button>)}</div><p className="text-xs text-muted-foreground">A ceiling, not a quota. The session ends when meaningful coverage is complete.</p></fieldset>

                <fieldset className="space-y-3"><legend className="text-sm font-medium">Target level</legend><div className="grid gap-2 sm:grid-cols-3">{LEVELS.map((item) => <button key={item.value} type="button" onClick={() => setLevel(item.value)} aria-pressed={level === item.value} className={cn("rounded-lg border p-3 text-left transition-colors", level === item.value ? "border-primary bg-primary/5" : "border-border hover:bg-accent/50")}><span className="block text-sm font-medium">{item.label}</span><span className="mt-1 block text-xs text-muted-foreground">{item.note}</span></button>)}</div></fieldset>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5"><Label htmlFor="feedback-mode">Feedback</Label><Select value={mode} onValueChange={(value) => setMode(value as InterviewMode)}><SelectTrigger id="feedback-mode"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="realistic">Realistic · report at end</SelectItem><SelectItem value="guided">Guided · coach each turn</SelectItem></SelectContent></Select></div>
                  <div className="space-y-1.5"><Label htmlFor="interview-format">Format</Label><Select value={format} onValueChange={(value) => setFormat(value as InterviewFormatChoice)}><SelectTrigger id="interview-format"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="auto">Detect from source</SelectItem><SelectItem value="concept">Concept interview</SelectItem><SelectItem value="system_design">System design</SelectItem><SelectItem value="source_led">Follow source sequence</SelectItem></SelectContent></Select></div>
                </div>

                <MicrophoneSetup
                  disabled={busy}
                  onReadyChange={setMicrophoneReady}
                />

                <Button
                  className="w-full"
                  size="lg"
                  disabled={!payload || busy || Boolean(preview && !microphoneReady)}
                  onClick={() => void (preview ? begin() : review())}
                >
                  {busy ? "Preparing…" : preview ? "Start interview" : "Review setup"}<ArrowRight aria-hidden />
                </Button>
                {preview && !microphoneReady ? (
                  <p className="text-center text-xs text-muted-foreground">
                    Test your microphone once before starting so the interview cannot begin on the wrong input.
                  </p>
                ) : null}
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card className={cn(!preview && "border-dashed")}>
                <CardHeader><CardTitle className="flex items-center gap-2"><Clock3 aria-hidden className="size-5" />Source preflight</CardTitle></CardHeader>
                <CardContent>
                  {preview ? (
                    <div className="space-y-4">
                      <div><p className="text-sm font-medium">{preview.title}</p><p className="mt-1 text-xs text-muted-foreground">{preview.source_title}</p></div>
                      <div className="grid grid-cols-2 gap-3"><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Detected format</p><p className="mt-1 text-sm font-medium capitalize">{preview.selected_format.replace("_", " ")}</p></div><div className="rounded-lg bg-muted p-3"><p className="text-xs text-muted-foreground">Estimated finish</p><p className="mt-1 text-sm font-medium">{preview.estimated_min_minutes}–{preview.estimated_max_minutes} min</p></div></div>
                      <p className="text-sm text-muted-foreground">{preview.required_topic_count} substantive topics will define coverage. Exact questions stay hidden.</p>
                      {preview.warnings.map((warning) => <Alert key={warning}><AlertDescription>{warning}</AlertDescription></Alert>)}
                    </div>
                  ) : (
                    <p className="text-sm leading-6 text-muted-foreground">Choose a source and review the setup. The preflight checks evidence readiness, detects the interview shape, and estimates an honest duration before any question is generated.</p>
                  )}
                </CardContent>
              </Card>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                <div className="rounded-xl border bg-card p-4"><ShieldCheck aria-hidden className="size-5 text-primary" /><p className="mt-3 text-sm font-medium">Grounded scoring</p><p className="mt-1 text-xs leading-5 text-muted-foreground">Corrections and suggested answers trace back to pages, timestamps, or verified external sources.</p></div>
                <div className="rounded-xl border bg-card p-4"><MessagesSquare aria-hidden className="size-5 text-primary" /><p className="mt-3 text-sm font-medium">Voice and screen ready</p><p className="mt-1 text-xs leading-5 text-muted-foreground">Adaptive microphone detection, natural interviewer speech, and explicit screen checkpoints—with no raw media retained.</p></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
