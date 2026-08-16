"use client";

import { Loader2, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api";
import type {
  ChapterListResponse,
  ChapterSummary,
  DeckJob,
} from "@/lib/deck-types";
import type { BookListResponse, BookSummary } from "@/lib/types";
import type { VideoListResponse, VideoSummary } from "@/lib/video-types";
import { videoState } from "@/lib/video-state";

type Mode = "book" | "video";

export function GenerateDeck({ onQueued }: { onQueued: (job: DeckJob) => void }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("book");
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [bookId, setBookId] = useState<string>("");
  const [nodeId, setNodeId] = useState<string>("");
  const [videoId, setVideoId] = useState<string>("");
  const [generationMode, setGenerationMode] = useState<"topic_generated" | "book_extracted">("topic_generated");
  const [loadingChapters, setLoadingChapters] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        const [bookPayload, videoPayload] = await Promise.all([
          apiFetch<BookListResponse>("/books"),
          apiFetch<VideoListResponse>("/videos"),
        ]);
        setBooks(bookPayload.books);
        setVideos(
          videoPayload.videos.filter((video) =>
            ["ready", "partial"].includes(videoState(video)),
          ),
        );
      } catch (caught) {
        setError((caught as Error).message || "Could not load your library.");
      }
    })();
  }, [open]);

  useEffect(() => {
    if (!bookId) {
      setChapters([]);
      return;
    }
    setLoadingChapters(true);
    setNodeId("");
    (async () => {
      try {
        const payload = await apiFetch<ChapterListResponse>(
          `/books/${bookId}/chapters`,
        );
        setChapters(payload.chapters);
      } catch (caught) {
        setError((caught as Error).message || "Could not load the chapters.");
      } finally {
        setLoadingChapters(false);
      }
    })();
  }, [bookId]);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setError("");
    try {
      const job = await apiFetch<DeckJob>("/decks", {
        method: "POST",
        body: JSON.stringify(
          mode === "book"
            ? {
                source_kind: "book",
                generation_mode: generationMode,
                book_id: Number(bookId),
                node_id: Number(nodeId),
              }
            : { source_kind: "video", video_id: videoId },
        ),
      });
      onQueued(job);
      setOpen(false);
    } catch (caught) {
      setError((caught as Error).message || "Could not start generating.");
    } finally {
      setSubmitting(false);
    }
  }, [bookId, generationMode, mode, nodeId, onQueued, videoId]);

  const ready = mode === "book" ? Boolean(bookId && nodeId) : Boolean(videoId);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus aria-hidden />
          New deck
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogTitle>Make a deck</DialogTitle>
        <DialogDescription>
          {mode === "book" && generationMode === "book_extracted"
            ? "Use questions already printed in a chapter. Printed solutions are preserved; missing answers are grounded in the chapter."
            : "One deck covers one chapter or lecture. Each generated card cites the page or moment it came from."}
        </DialogDescription>

        <div className="space-y-4 pt-2">
          <div className="flex gap-1 rounded-lg bg-muted p-1">
            {(["book", "video"] as Mode[]).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setMode(value)}
                aria-pressed={mode === value}
                className={
                  mode === value
                    ? "flex-1 rounded-md bg-background px-3 py-2 text-sm font-medium shadow-sm"
                    : "flex-1 rounded-md px-3 py-2 text-sm text-muted-foreground"
                }
              >
                {value === "book" ? "From a chapter" : "From a lecture"}
              </button>
            ))}
          </div>

          {mode === "book" ? (
            <>
              <div className="space-y-2">
                <Label htmlFor="deck-book">Book</Label>
                <Select value={bookId} onValueChange={setBookId}>
                  <SelectTrigger id="deck-book">
                    <SelectValue placeholder="Choose a book" />
                  </SelectTrigger>
                  <SelectContent>
                    {books.map((book) => (
                      <SelectItem
                        key={book.book_id}
                        value={String(book.book_id)}
                      >
                        {book.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="deck-chapter">Chapter</Label>
                {loadingChapters ? (
                  <Skeleton className="h-9 w-full" />
                ) : (
                  <Select
                    value={nodeId}
                    onValueChange={setNodeId}
                    disabled={chapters.length === 0}
                  >
                    <SelectTrigger id="deck-chapter">
                      <SelectValue
                        placeholder={
                          bookId ? "Choose a chapter" : "Choose a book first"
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {chapters.map((chapter) => (
                        <SelectItem
                          key={chapter.node_id}
                          value={String(chapter.node_id)}
                        >
                          {chapter.title} · pp. {chapter.start_page}–
                          {chapter.end_page}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </div>

              <div className="space-y-2 pt-1">
                <Label>Question source</Label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setGenerationMode("topic_generated")}
                    className={
                      generationMode === "topic_generated"
                        ? "rounded-md border border-primary bg-wash p-3 text-left text-xs font-medium"
                        : "rounded-md border bg-card p-3 text-left text-xs text-muted-foreground hover:bg-muted"
                    }
                  >
                    <div className="font-semibold text-foreground">Generate from topics</div>
                    <div className="mt-1 text-xs text-muted-foreground">AI writes new revision cards</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => setGenerationMode("book_extracted")}
                    className={
                      generationMode === "book_extracted"
                        ? "rounded-md border border-primary bg-wash p-3 text-left text-xs font-medium"
                        : "rounded-md border bg-card p-3 text-left text-xs text-muted-foreground hover:bg-muted"
                    }
                  >
                    <div className="font-semibold text-foreground">Use questions from book</div>
                    <div className="mt-1 text-xs text-muted-foreground">Preserve printed exercises and answers</div>
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="deck-video">Lecture</Label>
              <Select value={videoId} onValueChange={setVideoId}>
                <SelectTrigger id="deck-video">
                  <SelectValue placeholder="Choose a lecture" />
                </SelectTrigger>
                <SelectContent>
                  {videos.map((video) => (
                    <SelectItem key={video.video_id} value={video.video_id}>
                      {video.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {videos.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No processed lectures yet.
                </p>
              ) : null}
            </div>
          )}

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button disabled={!ready || submitting} onClick={() => void submit()}>
              {submitting ? (
                <Loader2 aria-hidden className="animate-spin" />
              ) : null}
              Generate
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
