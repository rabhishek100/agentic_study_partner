"use client";

import { ArrowDown, ArrowUp, ExternalLink, Trash2 } from "lucide-react";
import Link from "next/link";

import { VideoPoster } from "@/components/video/video-poster";
import { stageLabel } from "@/components/video/ingestion-status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CourseLecture } from "@/lib/course-types";
import { etaWindow, isActiveVideoJob } from "@/lib/ingestion-progress";
import { conciseLectureTitle } from "@/components/course/course-lecture-navigator";

export function CourseCurriculumManager({
  lectures,
  onMove,
  onDetach,
}: {
  lectures: CourseLecture[];
  onMove(videoId: string, index: number): void;
  onDetach(videoId: string): void;
}) {
  return (
    <section aria-labelledby="manage-curriculum-title" className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-8 sm:px-6">
        <div className="space-y-1">
          <h2 id="manage-curriculum-title" className="text-lg font-semibold">Manage curriculum</h2>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Reorder lectures or remove them from this course. Lecture media and evidence stay in your video library.
          </p>
        </div>
        <ol className="divide-y divide-divider border-y border-divider">
          {lectures.map((lecture, index) => {
            const title = conciseLectureTitle(lecture.display_title, index);
            return (
              <li key={lecture.video_id} className="grid gap-4 py-4 sm:grid-cols-[6.5rem_minmax(0,1fr)_auto] sm:items-center">
                <VideoPoster video={lecture} className="rounded-md" />
                <div className="min-w-0">
                  <p className="text-xs tabular-nums text-muted-foreground">Lecture {index + 1}</p>
                  <p className="font-medium">{title}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <Badge variant={lecture.ready_for_qa ? "outline" : lecture.readiness_status === "failed" ? "destructive" : "secondary"}>
                      {isActiveVideoJob(lecture.latest_ingestion)
                        ? "Upgrading"
                        : lecture.ready_for_qa
                          ? "Ready"
                          : lecture.readiness_status === "failed"
                            ? "Failed"
                            : "Processing"}
                    </Badge>
                    <Button variant="link" size="sm" asChild className="h-auto px-0">
                      <Link href={`/videos/${lecture.video_id}`}>Open lecture <ExternalLink aria-hidden /></Link>
                    </Button>
                  </div>
                  {isActiveVideoJob(lecture.latest_ingestion) ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {stageLabel(lecture.latest_ingestion?.stage ?? null)} · {etaWindow(lecture.latest_ingestion?.timing?.estimated_remaining_seconds)}
                    </p>
                  ) : null}
                </div>
                <div className="flex items-center justify-end gap-1">
                  <Button variant="ghost" size="icon-sm" disabled={index === 0} onClick={() => onMove(lecture.video_id, index - 1)} aria-label={`Move ${title} up`}><ArrowUp aria-hidden /></Button>
                  <Button variant="ghost" size="icon-sm" disabled={index === lectures.length - 1} onClick={() => onMove(lecture.video_id, index + 1)} aria-label={`Move ${title} down`}><ArrowDown aria-hidden /></Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => onDetach(lecture.video_id)} aria-label={`Remove ${title} from course`} className="text-destructive"><Trash2 aria-hidden /></Button>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
