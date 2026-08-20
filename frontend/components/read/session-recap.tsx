"use client";

import { Layers, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  DropdownMenuItem,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import { apiFetch } from "@/lib/api";
import type { Anchor } from "@/lib/types";

export interface Chapter {
  node_id: number;
  title: string;
  start_page: number;
  end_page: number;
}

export interface Recap {
  questionCount: number;
  firstPage: number | null;
  lastPage: number | null;
  /** The section the reader asked about most, which a deck can be made from. */
  busiest: Chapter | null;
}

/**
 * What a reading session covered, derived rather than generated.
 *
 * No model call: the pages come from the anchors, the sections from the
 * outline the interface already holds, and "the section you asked about most"
 * is a count. A prose summary would be nicer to read and would need an LLM
 * call on every session — worth adding when something measures that the count
 * is not enough, and not before.
 */
export function recapOf(anchors: Anchor[][], chapters: Chapter[]): Recap {
  const pages = anchors
    .flatMap((forThread) => forThread)
    .flatMap((anchor) =>
      anchor.kind === "document_page" || anchor.kind === "document_passage"
        ? [anchor.page]
        : [],
    );
  if (pages.length === 0) {
    return { questionCount: 0, firstPage: null, lastPage: null, busiest: null };
  }

  const counts = new Map<number, number>();
  for (const page of pages) {
    const chapter = chapters.find(
      (candidate) => page >= candidate.start_page && page <= candidate.end_page,
    );
    if (!chapter) continue;
    counts.set(chapter.node_id, (counts.get(chapter.node_id) ?? 0) + 1);
  }
  let busiest: Chapter | null = null;
  let best = 0;
  for (const chapter of chapters) {
    const count = counts.get(chapter.node_id) ?? 0;
    if (count > best) {
      busiest = chapter;
      best = count;
    }
  }

  return {
    questionCount: pages.length,
    firstPage: Math.min(...pages),
    lastPage: Math.max(...pages),
    busiest,
  };
}

/**
 * What a session becomes when the reader is done with it.
 *
 * A deck from the section they spent the session in, which is the handoff the
 * spec names — and it is a call to the deck generator that already exists,
 * scoped by a chapter, rather than a second generator that knows about
 * sessions.
 *
 * It renders as part of the session menu rather than under the composer. Under
 * the composer it was one of four things stacked between the reader and the
 * field they type in, and its label — which interpolated a chapter title —
 * ran off its own edge. The section is named once, in the sentence above the
 * action, where a long title has a line to wrap onto.
 */
export function SessionRecap({
  recap,
  bookId,
}: {
  recap: Recap;
  bookId: number;
}) {
  const router = useRouter();
  const [state, setState] = useState<"idle" | "queuing" | "failed">("idle");

  if (recap.questionCount === 0) return null;

  const span =
    recap.firstPage === recap.lastPage
      ? `p. ${recap.firstPage}`
      : `pp. ${recap.firstPage}–${recap.lastPage}`;

  async function makeDeck() {
    if (!recap.busiest) return;
    setState("queuing");
    try {
      await apiFetch("/decks", {
        method: "POST",
        body: JSON.stringify({
          source_kind: "book",
          generation_mode: "topic_generated",
          book_id: bookId,
          node_id: recap.busiest.node_id,
        }),
      });
      router.push("/decks");
    } catch {
      setState("failed");
    }
  }

  return (
    <>
      <DropdownMenuLabel className="font-normal text-muted-foreground">
        {recap.questionCount === 1
          ? "1 question"
          : `${recap.questionCount} questions`}{" "}
        across {span}
        {recap.busiest ? `, mostly in ${recap.busiest.title}` : ""}.
      </DropdownMenuLabel>
      {recap.busiest && (
        <DropdownMenuItem
          disabled={state === "queuing"}
          // Queuing is a request, not a navigation: closing the menu on the
          // click would take its own failure message with it.
          onSelect={(event) => {
            event.preventDefault();
            void makeDeck();
          }}
        >
          {state === "queuing" ? (
            <Loader2 aria-hidden className="animate-spin" />
          ) : (
            <Layers aria-hidden />
          )}
          Make a deck from that section
        </DropdownMenuItem>
      )}
      {state === "failed" && (
        <DropdownMenuLabel
          role="alert"
          className="font-normal text-destructive"
        >
          The deck could not be queued. Try again from the Cards page.
        </DropdownMenuLabel>
      )}
    </>
  );
}
