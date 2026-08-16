"use client";

import { Loader2, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { DeckPreferences } from "@/lib/deck-types";

export function CardsSettings({
  preferences,
  reviewedToday,
  onSave,
}: {
  preferences: DeckPreferences | null;
  reviewedToday: number;
  onSave: (preferences: DeckPreferences) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [newPerDay, setNewPerDay] = useState(10);
  const [reviewCeiling, setReviewCeiling] = useState(120);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!preferences) return;
    setNewPerDay(preferences.new_cards_per_day);
    setReviewCeiling(preferences.max_reviews_per_day);
  }, [preferences]);

  async function save() {
    if (!preferences) return;
    setSaving(true);
    try {
      await onSave({
        new_cards_per_day: Math.min(200, Math.max(0, newPerDay)),
        max_reviews_per_day: Math.min(1_000, Math.max(1, reviewCeiling)),
      });
      setOpen(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Settings2 aria-hidden />
          <span className="hidden sm:inline">Cards settings</span>
          <span className="sm:hidden">Settings</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogTitle>Cards settings</DialogTitle>
        <DialogDescription>
          Set a sustainable daily pace. These limits apply across every deck.
        </DialogDescription>
        <div className="space-y-4 pt-2">
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
              New cards are added after anything already due.
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
              onChange={(event) => setReviewCeiling(Number(event.target.value))}
            />
            <p className="text-xs text-muted-foreground">
              {reviewedToday} card{reviewedToday === 1 ? "" : "s"} reviewed today.
            </p>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button disabled={!preferences || saving} onClick={() => void save()}>
              {saving ? <Loader2 aria-hidden className="animate-spin" /> : null}
              Save settings
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
