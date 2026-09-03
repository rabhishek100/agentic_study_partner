"use client";

import { ArrowRight, GraduationCap, LogOut } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { AddCourseDialog } from "@/components/course/add-course-dialog";
import { CoursePoster } from "@/components/course/course-poster";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
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
import { Skeleton } from "@/components/ui/skeleton";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import type { CourseListResponse } from "@/lib/course-types";

const POLL_INTERVAL_MS = 5_000;

export default function CoursesPage() {
  const { session, sessionLoading } = useSession();
  const [payload, setPayload] = useState<CourseListResponse>({ courses: [] });
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setPayload(await apiFetch<CourseListResponse>("/courses"));
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not load your courses.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  const processing = payload.courses.reduce(
    (total, course) => total + course.processing_count,
    0,
  );
  useEffect(() => {
    if (!session || processing === 0) return;
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [session, processing, load]);

  if (sessionLoading) {
    return (
      <div className="grid h-dvh place-items-center p-6">
        <Skeleton className="h-6 w-48" />
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
      nav={<SectionNav active="courses" />}
      status={
        <span>
          {payload.courses.length} course
          {payload.courses.length === 1 ? "" : "s"}
          {processing ? ` · ${processing} lectures processing` : ""}
        </span>
      }
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
        <div className="flex h-full flex-col gap-6 overflow-y-auto p-4">
          <div className="sm:hidden">
            <SectionNav active="courses" />
          </div>
          <AddCourseDialog onAdded={load} />
          <div className="space-y-2 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">Course answers</p>
            <p>
              Search all ready lectures together. Every citation names the
              lecture and opens its exact timestamp or document page.
            </p>
          </div>
        </div>
      }
    >
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-8">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <h1 className="font-serif text-xl font-semibold tracking-tight">
              Courses
            </h1>
            <p className="text-xs text-muted-foreground">
              Ordered lectures with one grounded question space.
            </p>
          </div>
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {!loaded ? (
            <div className="grid gap-4 md:grid-cols-2">
              <Skeleton className="h-40 rounded-lg" />
              <Skeleton className="h-40 rounded-lg" />
            </div>
          ) : payload.courses.length === 0 ? (
            <section className="grid min-h-80 place-items-center rounded-xl border border-dashed border-border bg-surface p-8 text-center">
              <div className="max-w-md space-y-4">
                <GraduationCap
                  aria-hidden
                  className="mx-auto size-9 text-primary"
                />
                <div className="space-y-1">
                  <h2 className="font-serif text-lg font-semibold">
                    Build your first course
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    Paste a YouTube playlist or an ordered set of lectures,
                    let the video pipeline ingest each one, then ask across
                    the set.
                  </p>
                </div>
                <div className="mx-auto w-44">
                  <AddCourseDialog onAdded={load} />
                </div>
              </div>
            </section>
          ) : (
            <ul className="grid gap-4 md:grid-cols-2">
              {payload.courses.map((course) => (
                <li key={course.course_id}>
                  <Link
                    href={`/courses/${course.course_id}`}
                    className="group flex h-full flex-col overflow-hidden rounded-xl border border-border bg-card transition-colors hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <CoursePoster
                      title={course.title}
                      youtubeVideoId={course.preview_youtube_video_id}
                      lectureCount={course.lecture_count}
                      className="rounded-none border-0 border-b border-divider"
                    />
                    <div className="flex flex-1 flex-col justify-between p-5">
                      <div className="space-y-2">
                        <div className="flex items-start justify-between gap-3">
                          <h2 className="font-serif text-lg font-semibold tracking-tight">
                            {course.title}
                          </h2>
                          <ArrowRight
                            aria-hidden
                            className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                          />
                        </div>
                        {course.description ? (
                          <p className="line-clamp-2 text-sm text-muted-foreground">
                            {course.description}
                          </p>
                        ) : null}
                      </div>
                      <div className="mt-5 flex flex-wrap gap-2">
                        {course.ready_count ? (
                          <Badge variant="outline">{course.ready_count} ready</Badge>
                        ) : null}
                        {course.degraded_count ? (
                          <Badge variant="outline">
                            {course.degraded_count} with gaps
                          </Badge>
                        ) : null}
                        {course.processing_count ? (
                          <Badge variant="outline">
                            {course.processing_count} processing
                          </Badge>
                        ) : null}
                        {course.failed_count ? (
                          <Badge variant="destructive">
                            {course.failed_count} failed
                          </Badge>
                        ) : null}
                        <Badge variant="outline">
                          ${course.actual_ingestion_cost_usd.toFixed(2)} / $
                          {course.ingestion_cost_cap_usd.toFixed(2)} ingest
                        </Badge>
                      </div>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </main>
    </AppShell>
  );
}
