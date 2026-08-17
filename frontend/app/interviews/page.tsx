"use client";

import { Loader2, LogOut, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { InterviewSetup } from "@/components/interviews/interview-setup";
import { RecentInterviews } from "@/components/interviews/recent-interviews";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { primeInterviewerSpeech } from "@/hooks/use-interviewer-speech";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { signOut, useSession } from "@/hooks/use-session";
import {
  primeInterviewAudio,
  releasePrimedInterviewAudio,
} from "@/hooks/use-interview-voice";
import { apiFetch } from "@/lib/api";
import type { ChapterListResponse } from "@/lib/deck-types";
import type {
  InterviewPreflight,
  InterviewSession,
  InterviewSetupPayload,
} from "@/lib/interview-types";
import type { BookListResponse, BookSummary } from "@/lib/types";
import type { VideoListResponse, VideoSummary } from "@/lib/video-types";
import { videoState } from "@/lib/video-state";

/**
 * The route owns the network and the navigation; `InterviewSetup` owns every
 * setup decision and the two-phase commit. Keeping the seam there is what lets
 * the composition be reviewed against fixtures without a signed-in session.
 */
export default function InterviewsPage() {
  const router = useRouter();
  const { session, sessionLoading } = useSession();
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [history, setHistory] = useState<InterviewSession[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState("");

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
      setLoadError("");
    } catch (failure) {
      setLoadError((failure as Error).message || "Could not load interview setup.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  const fetchChapters = useCallback(async (bookId: string) => {
    const payload = await apiFetch<ChapterListResponse>(`/books/${bookId}/chapters`);
    return payload.chapters;
  }, []);

  const runPreflight = useCallback(
    (payload: InterviewSetupPayload) =>
      apiFetch<InterviewPreflight>("/interviews/preflight", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    [],
  );

  const startInterview = useCallback(
    async (
      payload: InterviewSetupPayload,
      report: (stage: "creating_session" | "generating_question" | "opening_workspace") => void,
    ) => {
      // Both priming calls must run inside the click's task: browser autoplay
      // policy unlocks audio on a user gesture, not on the promise that follows.
      primeInterviewAudio();
      primeInterviewerSpeech();
      try {
        const created = await apiFetch<InterviewSession>("/interviews", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        report("generating_question");
        await apiFetch<InterviewSession>(`/interviews/${created.session_id}/start`, {
          method: "POST",
        });
        report("opening_workspace");
        router.push(`/interviews/${created.session_id}`);
      } catch (failure) {
        releasePrimedInterviewAudio();
        throw failure;
      }
    },
    [router],
  );

  if (sessionLoading) {
    return (
      <div className="grid h-dvh place-items-center p-6">
        <div
          className="flex max-w-sm items-start gap-3 rounded-lg border border-divider bg-surface p-6"
          role="status"
          aria-live="polite"
        >
          <Loader2
            aria-hidden
            className="mt-1 size-5 shrink-0 animate-spin text-action motion-reduce:animate-none"
          />
          <div>
            <p className="font-medium">Checking your session</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Verifying sign-in before loading interview sources and history.
            </p>
          </div>
        </div>
      </div>
    );
  }
  if (!session) {
    return (
      <div className="relative grid h-dvh place-items-center p-6">
        <div className="absolute right-3 top-3">
          <ThemeToggle />
        </div>
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
            <DropdownMenuLabel className="font-normal text-muted-foreground">
              Signed in
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => signOut()}>
              <LogOut aria-hidden />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      }
      rail={
        <div className="flex h-full flex-col overflow-y-auto p-4">
          <div className="mb-6 sm:hidden">
            <SectionNav active="interviews" />
          </div>
          <RecentInterviews sessions={history} />
        </div>
      }
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-8 sm:py-12">
          <div className="max-w-2xl">
            <Badge variant="secondary" className="mb-4">
              <Sparkles aria-hidden /> Adaptive practice
            </Badge>
            <h1 className="font-serif text-3xl font-semibold tracking-tight sm:text-4xl">
              Practice the interview, not a question list.
            </h1>
            <p className="mt-3 text-base leading-7 text-muted-foreground">
              Choose one chapter or lecture. The interviewer follows its evidence,
              adapts to your answers, scores against the source, and finishes when
              the useful material is covered.
            </p>
          </div>

          {loadError ? (
            <Alert variant="destructive" className="mt-8">
              <AlertDescription>{loadError}</AlertDescription>
            </Alert>
          ) : null}

          <div className="mt-10">
            <InterviewSetup
              books={books}
              videos={videos}
              loaded={loaded}
              fetchChapters={fetchChapters}
              runPreflight={runPreflight}
              startInterview={startInterview}
            />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
