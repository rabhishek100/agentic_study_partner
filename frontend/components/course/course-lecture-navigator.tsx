"use client";

import { Check, Search, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { VideoPoster } from "@/components/video/video-poster";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import type { CourseLecture } from "@/lib/course-types";

export function conciseLectureTitle(title: string, index: number): string {
  return title.replace(
    new RegExp(`^(?:${index + 1}\\.\\s*)?Lecture\\s+${index + 1}:?\\s*`, "i"),
    "",
  );
}

export function CourseLectureNavigator({
  lectures,
  selected,
  selectionLocked,
  onSelectionChange,
  headingId = "lecture-scope-title",
}: {
  lectures: CourseLecture[];
  selected: string[];
  selectionLocked: boolean;
  onSelectionChange(next: string[]): void;
  headingId?: string;
}) {
  const [query, setQuery] = useState("");
  const readyIds = useMemo(
    () => lectures.filter((lecture) => lecture.ready_for_qa).map((lecture) => lecture.video_id),
    [lectures],
  );
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return lectures;
    return lectures.filter((lecture, index) =>
      `${index + 1} ${lecture.display_title}`.toLowerCase().includes(normalized),
    );
  }, [lectures, query]);
  const allReadySelected = readyIds.length > 0 && readyIds.every((id) => selected.includes(id));

  return (
    <section
      aria-labelledby={headingId}
      className="flex h-full min-h-0 flex-col border-b border-divider bg-surface lg:border-b-0 lg:border-r"
    >
      <div className="space-y-3 border-b border-divider p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 id={headingId} className="font-medium">Lecture scope</h2>
            <p className="text-xs text-muted-foreground">
              {selectionLocked ? "Fixed for this conversation" : "Choose what this question can search"}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={selectionLocked || readyIds.length === 0}
            onClick={() => onSelectionChange(allReadySelected ? [] : readyIds)}
            aria-pressed={allReadySelected}
          >
            {allReadySelected ? <Check aria-hidden /> : <SlidersHorizontal aria-hidden />}
            {allReadySelected ? "All selected" : "Select all"}
          </Button>
        </div>
        <div className="relative">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search lectures"
            aria-label="Search course lectures"
            className="pl-9"
          />
        </div>
      </div>

      <ol className="min-h-0 flex-1 divide-y divide-divider overflow-y-auto">
        {visible.map((lecture) => {
          const index = lectures.findIndex((item) => item.video_id === lecture.video_id);
          const checked = selected.includes(lecture.video_id);
          const title = conciseLectureTitle(lecture.display_title, index);
          return (
            <li
              key={lecture.video_id}
              className="grid grid-cols-[auto_4.5rem_minmax(0,1fr)] items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-hover"
            >
              <Checkbox
                checked={checked}
                disabled={!lecture.ready_for_qa || selectionLocked}
                onCheckedChange={(next) =>
                  onSelectionChange(
                    next
                      ? [...selected, lecture.video_id]
                      : selected.filter((id) => id !== lecture.video_id),
                  )
                }
                aria-label={`Include lecture ${index + 1}: ${title}`}
              />
              <Link
                href={`/videos/${lecture.video_id}`}
                aria-label={`Open lecture ${index + 1}: ${title}`}
                className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <VideoPoster video={lecture} className="rounded-md" />
              </Link>
              <div className="min-w-0">
                <span className="block text-xs tabular-nums text-muted-foreground">
                  Lecture {index + 1}
                </span>
                <Link
                  href={`/videos/${lecture.video_id}`}
                  className="line-clamp-2 text-sm font-medium leading-snug hover:underline"
                >
                  {title}
                </Link>
                {!lecture.ready_for_qa ? (
                  <span className="mt-1 block text-xs text-warning">
                    {lecture.readiness_status === "failed" ? "Ingestion failed" : "Processing"}
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      <div className="border-t border-divider px-4 py-3 text-xs text-muted-foreground">
        {selected.length} of {readyIds.length} ready lectures selected
      </div>
    </section>
  );
}
