import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { lectureLines } from "@/components/course/add-course-dialog";
import { CourseAnswer } from "@/components/course/course-answer";
import { CourseIngestionProgress } from "@/components/course/course-ingestion-progress";
import { conciseLectureTitle } from "@/components/course/course-lecture-navigator";
import { CoursePoster } from "@/components/course/course-poster";
import { SectionNav } from "@/components/section-nav";
import type {
  CourseCitationRef,
  CourseDetail,
  CourseEvidenceRef,
} from "@/lib/course-types";

describe("course creation input", () => {
  it("preserves line order and accepts optional local titles", () => {
    expect(
      lectureLines(
        "https://youtu.be/abcdefghijk\nAttention | https://youtu.be/lmnopqrstuv\n",
      ),
    ).toEqual([
      { url: "https://youtu.be/abcdefghijk" },
      { title: "Attention", url: "https://youtu.be/lmnopqrstuv" },
    ]);
  });
});

describe("course previews", () => {
  it("uses the first lecture thumbnail and identifies the playlist size", () => {
    render(
      <CoursePoster
        title="Distributed Systems"
        youtubeVideoId="abc123"
        lectureCount={20}
      />,
    );

    expect(document.querySelector("img")).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/abc123/mqdefault.jpg",
    );
    expect(screen.getByText("20 lectures")).toBeInTheDocument();
  });
});

describe("course lecture labels", () => {
  it("removes repeated playlist numbering from the visible title", () => {
    expect(conciseLectureTitle("Lecture 1: Introduction", 0)).toBe("Introduction");
    expect(conciseLectureTitle("2. Lecture 2: RPC and Threads", 1)).toBe("RPC and Threads");
  });
});

describe("course answer citations", () => {
  const evidence: CourseEvidenceRef[] = [
    {
      rank: 1,
      evidence_id: "e-1",
      modality: "transcript",
      excerpt: "Queries are compared with keys.",
      retrieval_method: "fts",
      score: 0.7,
      start_ms: 65_000,
      end_ms: 75_000,
      page_number: null,
      frame_id: null,
      visual_event_id: null,
      transcript_segment_id: 1,
      resource_page_id: null,
      resource_id: null,
      resource_title: null,
      video_id: "video-1",
      video_title: "Attention",
      lecture_index: 1,
      ingestion_version_id: "version-1",
    },
  ];
  const citations: CourseCitationRef[] = [
    {
      marker: "[S1]",
      evidence_rank: 1,
      video_id: "video-1",
      video_title: "Attention",
      lecture_index: 1,
      modality: "transcript",
      start_ms: 65_000,
      page_number: null,
      frame_id: null,
      resource_id: null,
    },
  ];

  it("names the lecture and links to its exact timestamp", () => {
    render(
      <CourseAnswer
        answer="The course introduces query-key comparison [S1]."
        evidence={evidence}
        citations={citations}
      />,
    );
    expect(screen.getByRole("link", { name: /L2 · 1:05/ })).toHaveAttribute(
      "href",
      "/videos/video-1?t=65",
    );
    expect(screen.getByText(/Lecture 2 · Attention/)).toBeInTheDocument();
  });
});

describe("course navigation", () => {
  it("exposes Courses as a peer library section", () => {
    render(<SectionNav active="courses" />);
    expect(screen.getByRole("link", { name: "Courses" })).toHaveAttribute(
      "href",
      "/courses",
    );
    expect(screen.getByRole("link", { name: "Courses" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

describe("course ingestion progress", () => {
  it("explains the outcome, remaining work, ETA, and optional technical detail", () => {
    const course: CourseDetail = {
      course_id: "course-1",
      title: "Distributed Systems",
      description: null,
      preview_youtube_video_id: "abc123",
      lecture_count: 1,
      ready_count: 1,
      degraded_count: 0,
      processing_count: 1,
      failed_count: 0,
      ingestion_cost_cap_usd: 4.25,
      actual_ingestion_cost_usd: 1.98,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      lectures: [
        {
          video_id: "video-1",
          title: "Lecture 1",
          display_title: "Lecture 1",
          title_override: null,
          lecture_index: 0,
          description: null,
          source_kind: "youtube",
          duration_ms: 4_800_000,
          readiness_status: "degraded",
          ready_for_qa: true,
          playback: { kind: "youtube", youtube_video_id: "abc123", media_url: null },
          latest_ingestion: {
            job_id: "job-1",
            video_id: "video-1",
            status: "queued",
            stage: "embeddings",
            progress: { completed: 0, total: null, unit: null, percent: null },
            attempt: 2,
            max_attempts: 6,
            retryable: false,
            cancellation_requested: false,
            actual_cost_usd: "0.10",
            cost_cap_usd: "0.25",
            error: null,
            timing: {
              percent: 73,
              estimated_total_seconds: 900,
              estimated_remaining_seconds: 3600,
              overrunning: false,
              stages: [],
            },
          },
          readiness_notes: [],
          poster_frame_id: null,
          chapter_count: 12,
          slide_count: 0,
          deletable: false,
          created_at: "2026-09-01T00:00:00Z",
          updated_at: "2026-09-01T00:00:00Z",
          ready_at: "2026-09-01T00:00:00Z",
        },
      ],
    };

    render(<CourseIngestionProgress course={course} />);

    expect(screen.getByText("Improving course search")).toBeInTheDocument();
    expect(screen.getByText(/About .* remaining/)).toBeInTheDocument();
    expect(screen.getByText("1 lecture is available now")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "View progress" }));

    expect(screen.getByText("Course upgrade")).toBeInTheDocument();
    expect(screen.getByText("What is happening")).toBeInTheDocument();
    expect(screen.getByText("Technical details")).toBeInTheDocument();
    expect(screen.getByText(/Ask across the course now/)).toBeInTheDocument();
  });
});
