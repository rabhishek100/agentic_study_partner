"use client";

import {
  BellRing,
  BookOpen,
  FileText,
  Loader2,
  Settings2,
  Video,
} from "lucide-react";
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
import type {
  AutomaticSetActivationResponse,
  DeckPreferences,
  DeckSourcePreference,
} from "@/lib/deck-types";
import {
  browserNotificationState,
  rememberBrowserAlertsEnabled,
  requestBrowserNotificationPermission,
  type BrowserNotificationState,
  type DeckReminderPreferences,
} from "@/lib/notification-types";

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
  if (
    source.automatic_cards_activated &&
    source.missing_automatic_set_count === 0
  ) {
    return "Automatic Set 1 active";
  }
  if (source.can_activate_automatic_cards) {
    return `${missingScopeLabel(source)} ready to create`;
  }
  if (["ready", "degraded"].includes(source.status)) {
    return source.missing_automatic_set_count === 0
      ? "Included in Today · Set 1 already exists"
      : "Included in Today";
  }
  if (source.status === "failed") return "Source processing failed";
  return "Processing · Cards will start when ready";
}

function missingScopeLabel(source: DeckSourcePreference): string {
  const count = source.missing_automatic_set_count;
  if (source.document_type === "book") {
    return `${count} missing chapter set${count === 1 ? "" : "s"}`;
  }
  return `Automatic Set 1`;
}

function activationButtonLabel(source: DeckSourcePreference): string {
  return source.document_type === "book"
    ? `Create ${source.missing_automatic_set_count} missing set${
        source.missing_automatic_set_count === 1 ? "" : "s"
      }`
    : "Create automatic Set 1";
}

function activationConfirmation(source: DeckSourcePreference): string {
  const count = source.missing_automatic_set_count;
  if (source.document_type === "book") {
    return `This queues ${count} chapter set${count === 1 ? "" : "s"} now.`;
  }
  return `This queues one complete ${
    source.document_type === "paper" ? "paper" : "lecture"
  } set now.`;
}

function effectiveReminderTimezone(
  preferences: DeckReminderPreferences,
): string {
  const detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return !preferences.enabled && preferences.timezone === "UTC"
    ? detectedTimezone || "UTC"
    : preferences.timezone;
}

function deviceTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function CardsSettings({
  preferences,
  sources,
  reviewedToday,
  onSave,
  onActivate,
  reminderPreferences,
  reminderLoadError,
  onRetryReminder,
  onSaveReminder,
}: {
  preferences: DeckPreferences | null;
  sources: DeckSourcePreference[];
  reviewedToday: number;
  onSave: (
    preferences: DeckPreferences,
    sources: DeckSourcePreference[],
  ) => Promise<void>;
  onActivate: (
    source: DeckSourcePreference,
    expectedMissingSetCount: number,
  ) => Promise<AutomaticSetActivationResponse>;
  reminderPreferences: DeckReminderPreferences | null;
  reminderLoadError: string;
  onRetryReminder: () => Promise<void>;
  onSaveReminder: (
    preferences: DeckReminderPreferences,
  ) => Promise<DeckReminderPreferences>;
}) {
  const [open, setOpen] = useState(false);
  const [newPerDay, setNewPerDay] = useState(10);
  const [reviewCeiling, setReviewCeiling] = useState(120);
  const [sourceSelections, setSourceSelections] = useState<
    Record<string, boolean>
  >({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [activationCandidate, setActivationCandidate] = useState<string | null>(
    null,
  );
  const [activating, setActivating] = useState<string | null>(null);
  const [activationError, setActivationError] = useState("");
  const [activationStatus, setActivationStatus] = useState("");
  const [reminderEnabled, setReminderEnabled] = useState(false);
  const [reminderTime, setReminderTime] = useState("09:00");
  const [reminderTimezone, setReminderTimezone] = useState("UTC");
  const [savingReminder, setSavingReminder] = useState(false);
  const [reminderError, setReminderError] = useState("");
  const [reminderStatus, setReminderStatus] = useState("");
  const [browserState, setBrowserState] =
    useState<BrowserNotificationState>("default");
  const [requestingBrowser, setRequestingBrowser] = useState(false);

  useEffect(() => {
    if (!preferences) return;
    setNewPerDay(preferences.new_cards_per_day);
    setReviewCeiling(preferences.max_reviews_per_day);
  }, [preferences]);

  useEffect(() => {
    if (!reminderPreferences) return;
    setReminderEnabled(reminderPreferences.enabled);
    setReminderTime(reminderPreferences.reminder_time);
    setReminderTimezone(effectiveReminderTimezone(reminderPreferences));
  }, [reminderPreferences]);

  useEffect(() => {
    setBrowserState(browserNotificationState());
  }, []);

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

  async function activate(source: DeckSourcePreference) {
    const key = `${source.source_kind}:${source.source_id}`;
    setActivating(key);
    setActivationError("");
    setActivationStatus("");
    try {
      const result = await onActivate(
        source,
        source.missing_automatic_set_count,
      );
      setActivationCandidate(null);
      setActivationStatus(
        result.jobs_queued === 0
          ? `Automatic Set 1 is already active for ${source.title}.`
          : result.jobs_queued === 1
          ? `Automatic Set 1 queued for ${source.title}.`
          : `${result.jobs_queued} automatic sets queued for ${source.title}.`,
      );
    } catch (caught) {
      setActivationError(
        (caught as Error).message ||
          "Could not activate automatic cards. Nothing new was queued.",
      );
    } finally {
      setActivating(null);
    }
  }

  async function saveReminder() {
    if (!reminderPreferences) return;
    setSavingReminder(true);
    setReminderError("");
    setReminderStatus("");
    try {
      const saved = await onSaveReminder({
        enabled: reminderEnabled,
        reminder_time: reminderTime,
        timezone: reminderTimezone,
      });
      setReminderEnabled(saved.enabled);
      setReminderTime(saved.reminder_time);
      setReminderTimezone(saved.timezone);
      setReminderStatus(
        saved.enabled
          ? `Daily reminder saved for ${saved.reminder_time} ${saved.timezone}.`
          : "Daily review reminder turned off.",
      );
    } catch (caught) {
      setReminderError(
        (caught as Error).message || "Could not save your daily reminder.",
      );
    } finally {
      setSavingReminder(false);
    }
  }

  async function enableBrowserAlerts() {
    setRequestingBrowser(true);
    setReminderError("");
    setReminderStatus("");
    try {
      const permission = await requestBrowserNotificationPermission();
      setBrowserState(permission);
      if (permission === "granted") {
        rememberBrowserAlertsEnabled();
        setReminderStatus(
          "Browser alerts enabled while Mugensei is open in this browser.",
        );
      } else if (permission === "denied") {
        setReminderError(
          "Browser alerts are blocked. Allow notifications for this site in your browser settings.",
        );
      } else if (permission === "unsupported") {
        setReminderError(
          "This browser does not support page notifications. Your in-app reminders still appear in Notifications.",
        );
      }
    } catch (caught) {
      setReminderError(
        (caught as Error).message ||
          "Could not request browser notification permission.",
      );
    } finally {
      setRequestingBrowser(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) {
          setSaveError("");
          setActivationCandidate(null);
          setActivationError("");
          setActivationStatus("");
          setReminderError("");
          setReminderStatus("");
          setBrowserState(browserNotificationState());
          if (reminderPreferences) {
            setReminderEnabled(reminderPreferences.enabled);
            setReminderTime(reminderPreferences.reminder_time);
            setReminderTimezone(
              effectiveReminderTimezone(reminderPreferences),
            );
          }
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

          <fieldset className="space-y-3 border-t border-border pt-4">
            <legend className="flex items-center gap-2 text-sm font-medium">
              <BellRing aria-hidden className="size-4 text-primary" />
              Daily review reminder
            </legend>
            {reminderLoadError ? (
              <div className="rounded-md border border-destructive bg-destructive-wash p-3">
                <p role="alert" className="text-xs text-destructive">
                  {reminderLoadError}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void onRetryReminder()}
                >
                  Try reminder settings again
                </Button>
              </div>
            ) : null}
            <label
              htmlFor="daily-review-reminder-enabled"
              className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3"
            >
              <Checkbox
                id="daily-review-reminder-enabled"
                checked={reminderEnabled}
                disabled={!reminderPreferences || savingReminder}
                onCheckedChange={(checked) =>
                  setReminderEnabled(checked === true)
                }
                className="mt-1"
              />
              <span>
                <span className="block text-sm font-medium">
                  Remind me to review today’s cards
                </span>
                <span className="block text-xs leading-5 text-muted-foreground">
                  Saved reminders always appear in the notification center.
                </span>
              </span>
            </label>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
              <div className="space-y-2">
                <Label htmlFor="daily-reminder-time">Reminder time</Label>
                <Input
                  id="daily-reminder-time"
                  type="time"
                  value={reminderTime}
                  disabled={
                    !reminderPreferences || !reminderEnabled || savingReminder
                  }
                  onChange={(event) => setReminderTime(event.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Timezone: {reminderTimezone}
                </p>
                {deviceTimezone() !== reminderTimezone ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={savingReminder}
                    onClick={() => {
                      const timezone = deviceTimezone();
                      setReminderTimezone(timezone);
                      setReminderStatus(
                        `Using ${timezone}. Save reminder to apply this timezone.`,
                      );
                      setReminderError("");
                    }}
                  >
                    Use this device timezone
                  </Button>
                ) : null}
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={!reminderPreferences || savingReminder}
                onClick={() => void saveReminder()}
              >
                {savingReminder ? (
                  <Loader2
                    aria-hidden
                    className="animate-spin motion-reduce:animate-none"
                  />
                ) : null}
                Save reminder
              </Button>
            </div>
            <div className="rounded-md bg-muted p-3">
              <p className="text-xs leading-5 text-muted-foreground">
                Browser alerts are optional and work only while Mugensei is
                open. The in-app notification center keeps your reminder until
                you read or dismiss it.
              </p>
              {browserState === "granted" ? (
                <p className="mt-2 text-xs font-medium text-positive">
                  Browser alerts enabled on this browser
                </p>
              ) : browserState === "unsupported" ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  This browser does not support page notifications.
                </p>
              ) : browserState === "denied" ? (
                <p className="mt-2 text-xs text-destructive">
                  Browser alerts are blocked. Allow notifications for this
                  site in your browser settings.
                </p>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  disabled={requestingBrowser}
                  onClick={() => void enableBrowserAlerts()}
                >
                  {requestingBrowser ? (
                    <Loader2
                      aria-hidden
                      className="animate-spin motion-reduce:animate-none"
                    />
                  ) : null}
                  Enable on this browser
                </Button>
              )}
            </div>
            {reminderStatus ? (
              <p role="status" aria-live="polite" className="text-sm text-positive">
                {reminderStatus}
              </p>
            ) : null}
            {reminderError ? (
              <p role="alert" className="text-sm text-destructive">
                {reminderError}
              </p>
            ) : null}
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
                          const selected =
                            sourceSelections[key] ?? source.cards_enabled;
                          const canActivate =
                            selected &&
                            source.cards_enabled &&
                            source.can_activate_automatic_cards;
                          const confirming = activationCandidate === key;
                          const activationBusy = activating === key;
                          return (
                            <li
                              key={key}
                              className="border-b border-border last:border-b-0"
                            >
                              <div className="flex items-start gap-2">
                                <label
                                  htmlFor={inputId}
                                  className="flex min-w-0 flex-1 cursor-pointer items-start gap-3 px-3 py-3 hover:bg-muted"
                                >
                                  <Checkbox
                                    id={inputId}
                                    checked={selected}
                                    disabled={saving || activationBusy}
                                    onCheckedChange={(checked) => {
                                      if (checked !== true && confirming) {
                                        setActivationCandidate(null);
                                        setActivationError("");
                                      }
                                      setSourceSelections((current) => ({
                                        ...current,
                                        [key]: checked === true,
                                      }));
                                    }}
                                    className="mt-1"
                                  />
                                  <span className="min-w-0 flex-1">
                                    <span className="block truncate text-sm font-medium">
                                      {source.title}
                                    </span>
                                    <span className="block text-xs leading-5 text-muted-foreground">
                                      {sourceStatus({
                                        ...source,
                                        cards_enabled: selected,
                                      })}
                                    </span>
                                  </span>
                                </label>
                                {canActivate && !confirming ? (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    className="mr-3 mt-2 shrink-0"
                                    disabled={saving || activating !== null}
                                    onClick={() => {
                                      setActivationCandidate(key);
                                      setActivationError("");
                                      setActivationStatus("");
                                    }}
                                  >
                                    {activationButtonLabel(source)}
                                  </Button>
                                ) : null}
                              </div>
                              {confirming ? (
                                <div className="mx-3 mb-3 rounded-md border border-warning bg-warning-wash p-3">
                                  <p className="text-xs leading-5">
                                    {activationConfirmation(source)} Existing
                                    decks and review history stay unchanged.
                                    Pausing cancels queued work and stops running
                                    work at the next safe boundary.
                                  </p>
                                  <div className="mt-2 flex flex-wrap gap-2">
                                    <Button
                                      type="button"
                                      size="sm"
                                      variant="ghost"
                                      disabled={activationBusy}
                                      onClick={() => {
                                        setActivationCandidate(null);
                                        setActivationError("");
                                      }}
                                    >
                                      Cancel activation
                                    </Button>
                                    <Button
                                      type="button"
                                      size="sm"
                                      disabled={activationBusy}
                                      onClick={() => void activate(source)}
                                    >
                                      {activationBusy ? (
                                        <Loader2
                                          aria-hidden
                                          className="animate-spin motion-reduce:animate-none"
                                        />
                                      ) : null}
                                      {source.document_type === "book"
                                        ? `Create ${source.missing_automatic_set_count} set${
                                            source.missing_automatic_set_count ===
                                            1
                                              ? ""
                                              : "s"
                                          }`
                                        : "Create Set 1"}
                                    </Button>
                                  </div>
                                </div>
                              ) : null}
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
          {activationStatus ? (
            <p role="status" aria-live="polite" className="text-sm text-positive">
              {activationStatus}
            </p>
          ) : null}
          {activationError ? (
            <p role="alert" className="text-sm text-destructive">
              {activationError}
            </p>
          ) : null}
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
