"use client";

import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { apiFetch } from "@/lib/api";
import type { IngestionJob, OutlineEntry, OutlineReview } from "@/lib/types";

interface OutlineReviewEditorProps {
  jobId: string;
  onConfirmed: (job: IngestionJob) => void;
}

// A proposal read off the book's own contents page is far stronger evidence
// than a typography sweep, and a reviewer who knows which one they are looking
// at reviews it differently.
const PROPOSAL_ORIGIN: Record<string, string> = {
  printed_contents: "These headings were read from the book's own contents page",
  transcribed_headings: "These headings were found in the transcribed text",
  deterministic_proposal: "These headings were inferred from typography",
  normalized_embedded: "These headings came from the PDF's embedded outline",
};

export function OutlineReviewEditor({
  jobId,
  onConfirmed,
}: OutlineReviewEditorProps) {
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState<OutlineReview | null>(null);
  const [entries, setEntries] = useState<OutlineEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiFetch<OutlineReview>(`/ingestions/${jobId}/toc-proposal`)
      .then((proposal) => {
        if (cancelled) return;
        setReview(proposal);
        setEntries(proposal.entries);
      })
      .catch((caught) => {
        if (!cancelled) {
          setError((caught as Error).message || "Could not load the outline.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  function update(index: number, change: Partial<OutlineEntry>) {
    setEntries((current) =>
      current.map((entry, position) =>
        position === index ? { ...entry, ...change } : entry,
      ),
    );
  }

  function remove(index: number) {
    setEntries((current) =>
      current.filter((_, position) => position !== index),
    );
  }

  function add() {
    const previous = entries.at(-1);
    setEntries((current) => [
      ...current,
      {
        level: previous?.level ?? 1,
        title: "New section",
        page: previous?.page ?? 1,
      },
    ]);
  }

  async function confirm() {
    setSaving(true);
    setError("");
    try {
      const job = await apiFetch<IngestionJob>(
        `/ingestions/${jobId}/toc-confirmation`,
        {
          method: "POST",
          body: JSON.stringify({ entries }),
        },
      );
      onConfirmed(job);
      setOpen(false);
    } catch (caught) {
      setError((caught as Error).message || "Could not confirm the outline.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" className="w-full">
          Review {entries.length || ""} headings
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] max-w-3xl grid-rows-[auto_minmax(0,1fr)_auto]">
        <DialogHeader>
          <DialogTitle>Review the proposed contents</DialogTitle>
          <DialogDescription>
            {PROPOSAL_ORIGIN[review?.outline_source ?? ""] ??
              "These headings were proposed automatically"}{" "}
            and are not saved as book content yet. Correct levels, titles, and
            PDF pages before continuing; this hierarchy controls section
            boundaries and citations.
          </DialogDescription>
          {review && (
            <p className="text-xs text-muted-foreground">
              {review.entries.length} proposed headings across{" "}
              {review.page_count} PDF pages
            </p>
          )}
        </DialogHeader>

        <div className="min-h-0 space-y-3">
          {review?.reasons.length ? (
            <details className="rounded-lg border border-border px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium">
                Why review is required
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-muted-foreground">
                {review.reasons.map((reason) => (
                  <li key={reason}>{reason.replaceAll("_", " ")}</li>
                ))}
              </ul>
            </details>
          ) : null}

          <div className="grid grid-cols-[4rem_minmax(0,1fr)_5rem_2rem] gap-2 px-2 text-xs font-medium text-muted-foreground">
            <span>Level</span>
            <span>Heading</span>
            <span>PDF page</span>
            <span className="sr-only">Actions</span>
          </div>
          <ScrollArea className="h-[50vh] rounded-lg border border-border">
            <ol className="space-y-2 p-2">
              {entries.map((entry, index) => (
                <li
                  key={index}
                  className="grid grid-cols-[4rem_minmax(0,1fr)_5rem_2rem] items-center gap-2"
                >
                  <Input
                    type="number"
                    min={1}
                    max={20}
                    value={entry.level}
                    aria-label={`Hierarchy level for heading ${index + 1}`}
                    onChange={(event) =>
                      update(index, { level: Number(event.target.value) })
                    }
                  />
                  <Input
                    value={entry.title}
                    aria-label={`Title for heading ${index + 1}`}
                    onChange={(event) =>
                      update(index, { title: event.target.value })
                    }
                  />
                  <Input
                    type="number"
                    min={1}
                    max={review?.page_count}
                    value={entry.page}
                    aria-label={`PDF page for heading ${index + 1}`}
                    onChange={(event) =>
                      update(index, { page: Number(event.target.value) })
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    disabled={entries.length === 1}
                    aria-label={`Remove heading ${index + 1}`}
                    onClick={() => remove(index)}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                </li>
              ))}
            </ol>
          </ScrollArea>
          <Button type="button" variant="outline" size="sm" onClick={add}>
            <Plus aria-hidden />
            Add heading
          </Button>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>

        <DialogFooter showCloseButton>
          <Button
            onClick={confirm}
            disabled={loading || saving || entries.length === 0}
          >
            {saving ? "Confirming…" : "Confirm and continue"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
