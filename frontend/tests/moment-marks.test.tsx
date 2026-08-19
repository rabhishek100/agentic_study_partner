import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  MomentMarks,
  anchoredMoment,
  momentMarksFor,
  nearestMark,
} from "@/components/watch/moment-marks";
import type { SideChatThread } from "@/lib/side-chat";
import type { Anchor } from "@/lib/types";

const VIDEO = "8b1f3c4e-0000-4000-8000-000000000001";

function thread(id: string, title: string, anchors: Anchor[]): SideChatThread {
  return {
    conversation_id: id,
    parent_conversation_id: "session",
    title,
    anchors,
    turn_count: 1,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
  };
}

const LATER = thread("b", "Why divide by the square root?", [
  { kind: "lecture_moment", anchor_id: "b1", video_id: VIDEO, timestamp_ms: 724_000 },
]);
const EARLIER = thread("a", "What is a key, exactly?", [
  {
    kind: "lecture_stretch",
    anchor_id: "a1",
    video_id: VIDEO,
    start_ms: 700_000,
    end_ms: 750_000,
  },
]);
const UNANCHORED = thread("c", "How does this compare to RNNs?", []);

describe("momentMarksFor", () => {
  it("keeps the questions that name a moment, in lecture order", () => {
    expect(momentMarksFor([LATER, EARLIER, UNANCHORED])).toEqual([
      { thread: EARLIER, atMs: 700_000 },
      { thread: LATER, atMs: 724_000 },
    ]);
  });

  it("takes a stretch by where it starts", () => {
    // The start is where the viewer was when they marked it, which is the
    // point on the timeline the mark belongs to.
    expect(anchoredMoment(EARLIER.anchors)).toBe(700_000);
    expect(anchoredMoment(LATER.anchors)).toBe(724_000);
    expect(anchoredMoment([])).toBeNull();
  });

  it("ignores an anchor that names a page rather than a moment", () => {
    const paged = thread("d", "Why is accuracy wrong?", [
      { kind: "document_page", anchor_id: "d1", book_id: 7, page: 108 },
    ]);

    expect(momentMarksFor([paged])).toEqual([]);
  });
});

describe("nearestMark", () => {
  it("finds the mark the playhead is closest to", () => {
    // A lecture has no page boundary, so "here" is proximity rather than
    // equality.
    const marks = momentMarksFor([EARLIER, LATER]);

    expect(nearestMark(marks, 702_000)?.thread).toBe(EARLIER);
    expect(nearestMark(marks, 723_000)?.thread).toBe(LATER);
    expect(nearestMark([], 0)).toBeNull();
  });
});

describe("MomentMarks", () => {
  it("shows nothing before anything has been asked", () => {
    const { container } = render(
      <MomentMarks marks={[]} atMs={0} onOpen={vi.fn()} onSeek={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("seeks to a mark and reopens its question", () => {
    // Both, because a question about 12:04 is unreadable while the lecture is
    // somewhere else.
    const onOpen = vi.fn();
    const onSeek = vi.fn();
    render(
      <MomentMarks
        marks={momentMarksFor([EARLIER, LATER])}
        atMs={0}
        onOpen={onOpen}
        onSeek={onSeek}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Why divide by the square root/ }),
    );

    expect(onSeek).toHaveBeenCalledWith(724_000);
    expect(onOpen).toHaveBeenCalledWith(LATER);
  });

  it("marks the one the lecture is nearest", () => {
    render(
      <MomentMarks
        marks={momentMarksFor([EARLIER, LATER])}
        atMs={723_000}
        onOpen={vi.fn()}
        onSeek={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: /Why divide by the square root/ }),
    ).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByRole("button", { name: /What is a key/ }),
    ).not.toHaveAttribute("aria-current");
  });
});
