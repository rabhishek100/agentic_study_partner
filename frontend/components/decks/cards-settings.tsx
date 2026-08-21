"use client";

import { BookOpen, FileText, Loader2, Settings2, Video } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { DeckPreferences, DeckSourcePreference } from "@/lib/deck-types";

type SourceGroup = {
  key: "books" | "papers" | "videos";
  title: string;
  description: string;
  icon: typeof BookOpen;
  sources: DeckSourcePreference[];
};

function sourceStatus(source: DeckSourcePreference): string {
  if (!source.cards_enabled) return "Paused · Saved decks remain available";
  if (source.automatic_cards_queued) return "Automatic Set 1 queued";
  if (["ready", "degraded"].includes(source.status)) {
    return "Included in Today";
  }
  if (source.status === "failed") return "Source processing failed";
  return "Processing · Cards will start when ready";
}

export function CardsSettings({
  preferences,
  sources,
  reviewedToday,
  onSave,
}: {
  preferences: DeckPreferences | null;
  sources: DeckSourcePreference[];
  reviewedToday: number;
  onSave: (
    preferences: DeckPreferences,
    sources: DeckSourcePreference[],
  ) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [newPerDay, setNewPerDay] = useState(10);
  const [reviewCeiling, setReviewCeiling] = useState(120);
  const [sourceSelections, setSourceSelections] = useState<
    Record<string, boolean>
  >({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    if (!preferences) return;
    setNewPerDay(preferences.new_cards_per_day);
    setReviewCeiling(preferences.max_reviews_per_day);
  }, [preferences]);

  useEffect(() => {
    if (open) return;
    setSourceSelections(
      Object.fromEntries(
        sources.map((source) => [
          `${source.source_kind}:${source.source_id}`,
          source.cards_enabled,
        ]),
      ),
    );
  }, [open, sources]);

  const groups: SourceGroup[] = [
    {
      key: "books",
      title: "Books",
      description:
        "Generate Set 1 for each chapter and include its cards in Today.",
      icon: BookOpen,
      sources: sources.filter(
        (source) =>
          source.source_kind === "book" && source.document_type !== "paper",
      ),
    },
    {
      key: "papers",
      title: "Papers",
      description:
        "Generate Set 1 for the complete paper and include its cards in Today.",
      icon: FileText,
      sources: sources.filter(
        (source) =>
          source.source_kind === "book" && source.document_type === "paper",
      ),
    },
    {
      key: "videos",
      title: "Lectures",
      description:
        "Generate Set 1 for each lecture and include its cards in Today.",
      icon: Video,
      sources: sources.filter((source) => source.source_kind === "video"),
    },
  ];

  async function save() {
    if (!preferences) return;
    setSaving(true);
    setSaveError("");
    try {
      await onSave(
        {
          new_cards_per_day: Math.min(200, Math.max(0, newPerDay)),
          max_reviews_per_day: Math.min(1_000, Math.max(1, reviewCeiling)),
        },
        sources.map((source) => ({
          ...source,
          cards_enabled:
            sourceSelections[`${source.source_kind}:${source.source_id}`] ??
            source.cards_enabled,
        })),
      );
      setOpen(false);
    } catch (caught) {
      setSaveError(
        (caught as Error).message ||
          "Could not save these settings. Your selections are still here.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) {
          setSaveError("");
          setSourceSelections(
            Object.fromEntries(
              sources.map((source) => [
                `${source.source_kind}:${source.source_id}`,
                source.cards_enabled,
              ]),
            ),
          );
        }
        setOpen(nextOpen);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" aria-label="Cards settings">
          <Settings2 aria-hidden />
          <span className="hidden sm:inline">Cards settings</span>
          <span className="sm:hidden">Settings</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogTitle>Cards settings</DialogTitle>
        <DialogDescription>
          Set a sustainable daily pace and choose which sources feed your
          review.
        </DialogDescription>
        <div className="max-h-[min(42rem,calc(100dvh-8rem))] space-y-5 overflow-y-auto px-1 pt-2">
          <fieldset className="grid gap-4 sm:grid-cols-2">
            <legend className="sr-only">Daily review pace</legend>
            <div className="space-y-2">
              <Label htmlFor="new-per-day">New cards per day</Label>
              <Input
                id="new-per-day"
                type="number"
                min={0}
                max={200}
                value={newPerDay}
                onChange={(event) => setNewPerDay(Number(event.target.value))}
              />
              <p className="text-xs text-muted-foreground">
                Added after anything already due.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="max-per-day">Review ceiling per day</Label>
              <Input
                id="max-per-day"
                type="number"
                min={1}
                max={1_000}
                value={reviewCeiling}
                onChange={(event) =>
                  setReviewCeiling(Number(event.target.value))
                }
              />
              <p className="text-xs text-muted-foreground">
                {reviewedToday} card{reviewedToday === 1 ? "" : "s"} reviewed
                today.
              </p>
            </div>
          </fieldset>

          <fieldset className="space-y-4 border-t border-border pt-4">
            <legend className="text-sm font-medium">Study sources</legend>
            <p className="text-xs leading-5 text-muted-foreground">
              Pausing a source removes it from Today and stops automatic cards.
              Its decks and review history stay saved.
            </p>
            {sources.length === 0 ? (
              <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
                Add a book, paper, or lecture to configure it here.
              </p>
            ) : (
              <div className="space-y-5">
                {groups.map((group) => {
                  if (group.sources.length === 0) return null;
                  const Icon = group.icon;
                  return (
                    <section
                      key={group.key}
                      aria-labelledby={`cards-${group.key}-heading`}
                    >
                      <div className="mb-2 flex items-start gap-2">
                        <Icon
                          aria-hidden
                          className="mt-1 size-4 shrink-0 text-primary"
                        />
                        <div>
                          <h3
                            id={`cards-${group.key}-heading`}
                            className="text-sm font-medium"
                          >
                            {group.title}
                          </h3>
                          <p className="text-xs leading-5 text-muted-foreground">
                            {group.description}
                          </p>
                        </div>
                      </div>
                      <ul className="overflow-hidden rounded-md border border-border">
                        {group.sources.map((source) => {
                          const key = `${source.source_kind}:${source.source_id}`;
                          const inputId = `cards-source-${source.source_kind}-${source.source_id}`;
                          return (
                            <li
                              key={key}
                              className="border-b border-border last:border-b-0"
                            >
                              <label
                                htmlFor={inputId}
                                className="flex cursor-pointer items-start gap-3 px-3 py-3 hover:bg-muted"
                              >
                                <Checkbox
                                  id={inputId}
                                  checked={
                                    sourceSelections[key] ??
                                    source.cards_enabled
                                  }
                                  disabled={saving}
                                  onCheckedChange={(checked) =>
                                    setSourceSelections((current) => ({
                                      ...current,
                                      [key]: checked === true,
                                    }))
                                  }
                                  className="mt-1"
                                />
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-sm font-medium">
                                    {source.title}
                                  </span>
                                  <span className="block text-xs leading-5 text-muted-foreground">
                                    {sourceStatus({
                                      ...source,
                                      cards_enabled:
                                        sourceSelections[key] ??
                                        source.cards_enabled,
                                    })}
                                  </span>
                                </span>
                              </label>
                            </li>
                          );
                        })}
                      </ul>
                    </section>
                  );
                })}
              </div>
            )}
          </fieldset>
          {saveError ? (
            <p role="alert" className="text-sm text-destructive">
              {saveError}
            </p>
          ) : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button
              variant="ghost"
              disabled={saving}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              disabled={!preferences || saving}
              onClick={() => void save()}
            >
              {saving ? (
                <Loader2
                  aria-hidden
                  className="animate-spin motion-reduce:animate-none"
                />
              ) : null}
              Save settings
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
