"use client";

import { Loader2, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { apiFetch } from "@/lib/api";

interface BatchCourseResponse {
  course_id: string;
}

export function lectureLines(value: string) {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const separator = line.lastIndexOf("|");
      if (separator === -1) return { url: line };
      const title = line.slice(0, separator).trim();
      const url = line.slice(separator + 1).trim();
      return { url, ...(title ? { title } : {}) };
    });
}

export function AddCourseDialog({ onAdded }: { onAdded?(): void }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"playlist" | "lectures">("playlist");
  const [playlistUrl, setPlaylistUrl] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [sources, setSources] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const lectures = useMemo(() => lectureLines(sources), [sources]);

  async function submit() {
    const playlistMode = mode === "playlist";
    if (
      saving ||
      (playlistMode ? !playlistUrl.trim() : !title.trim() || lectures.length === 0)
    )
      return;
    setSaving(true);
    setError("");
    try {
      const result = await apiFetch<BatchCourseResponse>(
        playlistMode
          ? "/courses/from-youtube-playlist"
          : "/courses/batch-youtube",
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify(
            playlistMode
              ? {
                  playlist_url: playlistUrl.trim(),
                  ...(title.trim() ? { title: title.trim() } : {}),
                  description: description.trim() || null,
                }
              : {
                  title: title.trim(),
                  description: description.trim() || null,
                  lectures,
                },
          ),
        },
      );
      setOpen(false);
      setMode("playlist");
      setPlaylistUrl("");
      setTitle("");
      setDescription("");
      setSources("");
      onAdded?.();
      router.push(`/courses/${result.course_id}`);
    } catch (caught) {
      setError((caught as Error).message || "Could not create the course.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="w-full justify-start">
          <Plus aria-hidden />
          Add course
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add a course</DialogTitle>
          <DialogDescription>
            Import a YouTube playlist or add an ordered video list. Each
            lecture uses the grounded, resumable ingestion pipeline.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div
            className="grid grid-cols-2 gap-1 rounded-lg bg-surface p-1"
            aria-label="Course source"
          >
            <Button
              type="button"
              variant={mode === "playlist" ? "secondary" : "ghost"}
              onClick={() => setMode("playlist")}
            >
              YouTube playlist
            </Button>
            <Button
              type="button"
              variant={mode === "lectures" ? "secondary" : "ghost"}
              onClick={() => setMode("lectures")}
            >
              Video list
            </Button>
          </div>
          {mode === "playlist" ? (
            <div className="space-y-2">
              <Label htmlFor="course-playlist">YouTube playlist URL</Label>
              <Input
                id="course-playlist"
                type="url"
                value={playlistUrl}
                onChange={(event) => setPlaylistUrl(event.target.value)}
                placeholder="https://www.youtube.com/playlist?list=…"
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                The playlist order and titles are snapshotted before any
                lecture jobs are created. Up to 100 videos.
              </p>
            </div>
          ) : null}
          <div className="space-y-2">
            <Label htmlFor="course-title">
              Course title {mode === "playlist" ? "(optional)" : ""}
            </Label>
            <Input
              id="course-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Transformers and Large Language Models"
              autoFocus={mode === "lectures"}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="course-description">Description (optional)</Label>
            <Textarea
              id="course-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={2}
              className="min-h-20 resize-y"
            />
          </div>
          {mode === "lectures" ? (
            <div className="space-y-2">
              <Label htmlFor="course-lectures">Lectures in course order</Label>
              <Textarea
                id="course-lectures"
                value={sources}
                onChange={(event) => setSources(event.target.value)}
                rows={8}
                className="min-h-44 resize-y font-mono text-xs"
                placeholder={
                  "https://youtu.be/…\nAttention | https://youtu.be/…"
                }
                aria-describedby="course-lecture-help"
              />
              <p
                id="course-lecture-help"
                className="text-xs text-muted-foreground"
              >
                One video per line. Optionally prefix a course-local title with
                <span className="font-mono"> Title | URL</span>.
              </p>
            </div>
          ) : null}
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground" aria-live="polite">
              {mode === "playlist"
                ? "Playlist metadata is checked before ingestion"
                : `${lectures.length} lecture${lectures.length === 1 ? "" : "s"}`}
            </p>
            <Button
              onClick={submit}
              disabled={
                saving ||
                (mode === "playlist"
                  ? !playlistUrl.trim()
                  : !title.trim() || lectures.length === 0)
              }
            >
              {saving ? <Loader2 aria-hidden className="animate-spin" /> : null}
              Create and ingest
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
