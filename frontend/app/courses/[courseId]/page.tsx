"use client";

import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  LayoutGrid,
  Loader2,
  MessageSquarePlus,
  Send,
  Square,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { CourseAnswer } from "@/components/course/course-answer";
import { CourseConversationPanel } from "@/components/course/course-conversation-panel";
import { CourseCurriculumManager } from "@/components/course/course-curriculum-manager";
import { CourseIngestionProgress } from "@/components/course/course-ingestion-progress";
import { CourseLectureNavigator } from "@/components/course/course-lecture-navigator";
import { CoursePoster } from "@/components/course/course-poster";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useSession } from "@/hooks/use-session";
import { API_BASE, apiFetch, errorDetail } from "@/lib/api";
import type {
  CourseAskResponse,
  CourseConversationDetail,
  CourseConversationListResponse,
  CourseConversationSummary,
  CourseDetail,
  CourseUiTurn,
} from "@/lib/course-types";
import { drainSseEvents } from "@/lib/sse";
import { accessToken } from "@/lib/supabase";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 5_000;

export default function CoursePage() {
  const { courseId } = useParams<{ courseId: string }>();
  const router = useRouter();
  const { session, sessionLoading } = useSession();
  const [course, setCourse] = useState<CourseDetail | null>(null);
  const [conversations, setConversations] = useState<
    CourseConversationSummary[]
  >([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<CourseUiTurn[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [workspaceMode, setWorkspaceMode] = useState<"study" | "manage">("study");
  const [loaded, setLoaded] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const selectionInitialized = useRef(false);

  const load = useCallback(async () => {
    try {
      const [detail, history] = await Promise.all([
        apiFetch<CourseDetail>(`/courses/${courseId}`),
        apiFetch<CourseConversationListResponse>(
          `/courses/${courseId}/conversations`,
        ),
      ]);
      setCourse(detail);
      setConversations(history.conversations);
      setSelected((current) => {
        if (!selectionInitialized.current) {
          selectionInitialized.current = true;
          return detail.lectures
            .filter((lecture) => lecture.ready_for_qa)
            .map((lecture) => lecture.video_id);
        }
        return current.filter((id) =>
          detail.lectures.some(
            (lecture) => lecture.video_id === id && lecture.ready_for_qa,
          ),
        );
      });
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not load this course.");
    } finally {
      setLoaded(true);
    }
  }, [courseId]);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  const processing = course?.processing_count ?? 0;
  useEffect(() => {
    if (!session || processing === 0) return;
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [session, processing, load]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const activeConversation = conversations.find(
    (item) => item.conversation_id === conversationId,
  );
  const selectionLocked = Boolean(conversationId);

  async function openConversation(id: string) {
    if (streaming) return;
    const detail = await apiFetch<CourseConversationDetail>(
      `/course-conversations/${id}`,
    );
    setConversationId(id);
    setSelected(detail.selected_video_ids);
    setTurns(
      detail.turns.map((turn) => ({
        id: `${id}-${turn.turn_index}`,
        question: turn.question,
        answer: turn.answer ?? "",
        result: turn.result,
        status: turn.result ? "complete" : "failed",
      })),
    );
    setWorkspaceMode("study");
  }

  function newConversation() {
    abortRef.current?.abort();
    setConversationId(null);
    setTurns([]);
    setSelected(
      course?.lectures
        .filter((lecture) => lecture.ready_for_qa)
        .map((lecture) => lecture.video_id) ?? [],
    );
    setError("");
    setWorkspaceMode("study");
  }

  async function ensureConversation(): Promise<string> {
    if (conversationId) return conversationId;
    const created = await apiFetch<CourseConversationSummary>(
      `/courses/${courseId}/conversations`,
      {
        method: "POST",
        body: JSON.stringify({ video_ids: selected }),
      },
    );
    setConversationId(created.conversation_id);
    setConversations((current) => [created, ...current]);
    return created.conversation_id;
  }

  async function ask() {
    const value = question.trim();
    if (!value || streaming || selected.length === 0) return;
    setQuestion("");
    setError("");
    const localId = crypto.randomUUID();
    setTurns((current) => [
      ...current,
      {
        id: localId,
        question: value,
        answer: "",
        result: null,
        status: "streaming",
      },
    ]);
    setStreaming(true);
    try {
      const id = await ensureConversation();
      const token = await accessToken();
      const controller = new AbortController();
      abortRef.current = controller;
      const response = await fetch(
        `${API_BASE}/course-conversations/${id}/turns/stream`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ question: value }),
          signal: controller.signal,
        },
      );
      if (!response.ok || !response.body) throw new Error(await errorDetail(response));
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value: bytes } = await reader.read();
        buffer += decoder.decode(bytes, { stream: !done });
        const drained = drainSseEvents(buffer);
        buffer = drained.rest;
        for (const event of drained.events) {
          if (event.event === "token") {
            const tokenPayload = JSON.parse(event.data) as { text: string };
            setTurns((current) =>
              current.map((turn) =>
                turn.id === localId
                  ? { ...turn, answer: turn.answer + tokenPayload.text }
                  : turn,
              ),
            );
          } else if (event.event === "final") {
            const payload = JSON.parse(event.data) as CourseAskResponse;
            setTurns((current) =>
              current.map((turn) =>
                turn.id === localId
                  ? {
                      ...turn,
                      answer: payload.result.answer,
                      result: payload.result,
                      status: "complete",
                    }
                  : turn,
              ),
            );
            void load();
          } else if (event.event === "error") {
            const detail = JSON.parse(event.data) as { detail?: string };
            throw new Error(detail.detail || "Course question failed.");
          }
        }
        if (done) break;
      }
    } catch (caught) {
      const message =
        (caught as Error).name === "AbortError"
          ? "Stopped."
          : (caught as Error).message || "Could not answer that question.";
      setTurns((current) =>
        current.map((turn) =>
          turn.id === localId
            ? { ...turn, status: "failed", error: message }
            : turn,
        ),
      );
    } finally {
      abortRef.current = null;
      setStreaming(false);
    }
  }

  async function move(videoId: string, index: number) {
    try {
      setCourse(
        await apiFetch<CourseDetail>(
          `/courses/${courseId}/lectures/${videoId}`,
          { method: "PATCH", body: JSON.stringify({ lecture_index: index }) },
        ),
      );
    } catch (caught) {
      setError((caught as Error).message || "Could not reorder that lecture.");
    }
  }

  async function detach(videoId: string) {
    try {
      const detail = await apiFetch<CourseDetail>(
        `/courses/${courseId}/lectures/${videoId}`,
        { method: "DELETE" },
      );
      setCourse(detail);
      setSelected((current) => current.filter((id) => id !== videoId));
    } catch (caught) {
      setError((caught as Error).message || "Could not remove that lecture.");
    }
  }

  async function removeCourse() {
    if (!course || !window.confirm(`Remove “${course.title}” as a course? The lecture files and evidence will be kept.`)) return;
    try {
      await apiFetch(`/courses/${courseId}`, { method: "DELETE" });
      router.push("/courses");
    } catch (caught) {
      setError((caught as Error).message || "Could not remove this course.");
    }
  }

  const selectedNames = useMemo(
    () =>
      course?.lectures.filter((lecture) => selected.includes(lecture.video_id)) ?? [],
    [course, selected],
  );

  if (sessionLoading) {
    return <div className="grid h-dvh place-items-center"><Skeleton className="h-6 w-48" /></div>;
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
      railMode="drawer-only"
      section="courses"
      status={course ? (
        course.processing_count
          ? "Course upgrade in progress"
          : `${course.lecture_count} lectures`
      ) : "Course"}
      account={<AccountMenu email={session.user.email} />}
      rail={
        <div className="flex h-full flex-col gap-5 overflow-y-auto p-4">

          <Button variant="outline" asChild className="justify-start"><Link href="/courses"><ArrowLeft aria-hidden />All courses</Link></Button>
          <Button onClick={newConversation} className="justify-start"><MessageSquarePlus aria-hidden />New conversation</Button>
          <nav aria-label="Course conversations" className="space-y-1">
            <p className="px-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground">Conversations</p>
            {conversations.length === 0 ? <p className="px-2 py-2 text-xs text-muted-foreground">No questions yet.</p> : conversations.map((item) => (
              <button key={item.conversation_id} type="button" disabled={streaming} onClick={() => void openConversation(item.conversation_id)} className={`w-full rounded-md px-2 py-2 text-left text-sm disabled:cursor-not-allowed disabled:opacity-50 ${conversationId === item.conversation_id ? "bg-wash font-medium" : "text-muted-foreground hover:bg-surface-hover hover:text-foreground"}`}>
                <span className="block truncate">{item.title}</span>
                <span className="text-xs">{item.turn_count} turn{item.turn_count === 1 ? "" : "s"}</span>
              </button>
            ))}
          </nav>
        </div>
      }
    >
      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {!loaded || !course ? (
          <div className="mx-auto w-full max-w-6xl space-y-4 p-6"><Skeleton className="h-8 w-72" /><Skeleton className="h-96 w-full" /></div>
        ) : (
          <>
            <header className="border-b border-divider bg-surface px-4 py-3 sm:px-6">
              <div className="grid grid-cols-[6rem_minmax(0,1fr)] items-center gap-4 sm:grid-cols-[9rem_minmax(0,1fr)_auto]">
                <CoursePoster
                  title={course.title}
                  youtubeVideoId={course.preview_youtube_video_id}
                  lectureCount={course.lecture_count}
                  eager
                  className="w-full max-w-xs sm:max-w-none"
                />
                <div className="min-w-0">
                  <h1 className="line-clamp-2 font-serif text-xl font-semibold tracking-tight sm:text-2xl">{course.title}</h1>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                    <span>{course.lecture_count} lectures</span>
                    {course.processing_count ? (
                      <>
                        <span aria-hidden>·</span>
                        <span>Ready to study while search improves</span>
                      </>
                    ) : null}
                  </div>
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" className="col-span-2 w-full sm:col-span-1 sm:w-auto">Course details<ChevronDown aria-hidden /></Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem asChild><Link href="/courses"><ArrowLeft aria-hidden />All courses</Link></DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => void removeCourse()} className="text-destructive"><Trash2 aria-hidden />Remove course</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </header>
            <CourseIngestionProgress course={course} />
            <div className="flex min-h-11 shrink-0 items-end border-b border-divider bg-background px-4 sm:px-6">
              <div role="tablist" aria-label="Course workspace mode" className="flex self-stretch">
                {([
                  ["study", "Study", BookOpen],
                  ["manage", "Manage curriculum", LayoutGrid],
                ] as const).map(([mode, label, Icon]) => (
                  <button
                    key={mode}
                    role="tab"
                    type="button"
                    aria-selected={workspaceMode === mode}
                    onClick={() => setWorkspaceMode(mode)}
                    className={cn(
                      "relative flex items-center gap-2 px-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action",
                      workspaceMode === mode && "text-foreground after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-action",
                    )}
                  >
                    <Icon aria-hidden className="size-4" />
                    {label}
                  </button>
                ))}
              </div>
              {workspaceMode === "study" ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" className="mb-1 ml-auto xl:hidden"><MessageSquarePlus aria-hidden /><span className="hidden sm:inline">Conversations</span><span className="sr-only sm:hidden">Conversations</span></Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-72">
                    <DropdownMenuItem onSelect={newConversation}><MessageSquarePlus aria-hidden />New conversation</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    {conversations.map((item) => (
                      <DropdownMenuItem key={item.conversation_id} onSelect={() => void openConversation(item.conversation_id)}>
                        <span className="min-w-0 flex-1 truncate">{item.title}</span>
                        <span className="text-xs text-muted-foreground">{item.turn_count}</span>
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : null}
            </div>

            {error ? <div className="shrink-0 px-4 pt-3 sm:px-6"><Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert></div> : null}

            {workspaceMode === "manage" ? (
              <CourseCurriculumManager
                lectures={course.lectures}
                onMove={(videoId, index) => void move(videoId, index)}
                onDetach={(videoId) => void detach(videoId)}
              />
            ) : (
              <>
                <div className="shrink-0 border-b border-divider bg-surface px-4 py-2 lg:hidden">
                  <Sheet>
                    <SheetTrigger asChild>
                      <Button variant="outline" className="w-full justify-between">
                        <span>Lecture scope</span>
                        <span className="text-xs font-normal text-muted-foreground">{selected.length} selected</span>
                      </Button>
                    </SheetTrigger>
                    <SheetContent side="left" className="w-full max-w-sm bg-surface p-0">
                      <SheetTitle className="sr-only">Choose lecture scope</SheetTitle>
                      <SheetDescription className="sr-only">Choose which ready course lectures this conversation can search.</SheetDescription>
                      <CourseLectureNavigator
                        lectures={course.lectures}
                        selected={selected}
                        selectionLocked={selectionLocked}
                        onSelectionChange={setSelected}
                        headingId="mobile-lecture-scope-title"
                      />
                    </SheetContent>
                  </Sheet>
                </div>
                <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(17rem,0.72fr)_minmax(30rem,1.6fr)] xl:grid-cols-[minmax(22rem,0.95fr)_minmax(32rem,1.75fr)_minmax(15rem,0.55fr)]">
                  <div className="hidden min-h-0 lg:block">
                    <CourseLectureNavigator
                      lectures={course.lectures}
                      selected={selected}
                      selectionLocked={selectionLocked}
                      onSelectionChange={setSelected}
                    />
                  </div>
                  <section aria-label="Ask this course" className="flex min-h-0 min-w-0 flex-col bg-background">
                  <div className="min-h-0 flex-1 overflow-y-auto">
                    <div className="mx-auto flex w-full max-w-3xl flex-col gap-10 px-5 py-8 sm:px-8">
                      {turns.length === 0 ? (
                        <div className="grid min-h-72 place-items-center text-center">
                          <div className="max-w-xl space-y-4">
                            <span className="mx-auto grid size-10 place-items-center rounded-full bg-wash text-action"><BookOpen aria-hidden className="size-5" /></span>
                            <h2 className="font-serif text-2xl font-semibold tracking-tight">Study the course as one source</h2>
                            <p className="text-sm leading-6 text-muted-foreground">Compare ideas across lectures, trace how a concept develops, or find where a topic is taught. Every answer links back to the exact lecture evidence.</p>
                            <p className="text-xs text-muted-foreground">{selectedNames.length} ready lecture{selectedNames.length === 1 ? "" : "s"} in scope</p>
                          </div>
                        </div>
                      ) : turns.map((turn) => (
                        <article key={turn.id} className="space-y-5">
                          <h2 className="max-w-2xl font-serif text-xl font-semibold leading-snug tracking-tight">{turn.question}</h2>
                          {turn.status === "streaming" && !turn.answer ? <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />Searching selected lectures…</p> : null}
                          {turn.answer ? turn.result ? <CourseAnswer answer={turn.answer} evidence={turn.result.evidence} citations={turn.result.citations} /> : <p className="whitespace-pre-wrap font-serif text-base leading-7">{turn.answer}</p> : null}
                          {turn.result?.warnings.map((warning) => <p key={warning} className="text-xs text-muted-foreground">{warning}</p>)}
                          {turn.status === "failed" ? <Alert variant="destructive"><AlertDescription>{turn.error}</AlertDescription></Alert> : null}
                        </article>
                      ))}
                      <div ref={bottomRef} />
                    </div>
                  </div>
                  <div className="shrink-0 border-t border-divider bg-surface">
                    <div className="mx-auto w-full max-w-3xl space-y-2 px-4 py-3 sm:px-6">
                      <div className="flex items-end gap-2 rounded-xl border border-input bg-background p-2 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-action">
                        <Textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void ask(); } }} placeholder="Ask across the selected lectures…" aria-label="Ask across the selected course lectures" rows={1} className="min-h-10 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0" disabled={selected.length === 0} />
                        {streaming ? <Button variant="outline" size="icon" onClick={() => abortRef.current?.abort()} aria-label="Stop"><Square aria-hidden /></Button> : <Button size="icon" onClick={() => void ask()} disabled={!question.trim() || selected.length === 0} aria-label="Ask"><Send aria-hidden /></Button>}
                      </div>
                      <p className="text-center text-xs text-muted-foreground">{activeConversation ? `This conversation searches ${activeConversation.selected_video_ids.length} lectures.` : `${selected.length} ready lectures selected. Scope locks after the first question.`}</p>
                    </div>
                  </div>
                  </section>
                  <CourseConversationPanel
                    conversations={conversations}
                    activeId={conversationId}
                    streaming={streaming}
                    onNew={newConversation}
                    onOpen={(id) => void openConversation(id)}
                  />
                </div>
              </>
            )}
          </>
        )}
      </main>
    </AppShell>
  );
}
