"use client";

import { ArrowRight, Bell, CheckCheck, Loader2, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { apiFetch } from "@/lib/api";
import type {
  NotificationListResponse,
  StudyNotification,
} from "@/lib/notification-types";
import {
  browserAlertEnabledAt,
  rememberBrowserAlertsEnabled,
} from "@/lib/notification-types";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 30_000;
const DELIVERED_STORAGE_KEY = "mugensei:browser-notifications-delivered";
const TODAY_REVIEW_HREF = "/decks?review=today";

export function safeNotificationHref(href: string | null): string | null {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return null;
  return href;
}

export function notificationDestination(
  item: StudyNotification,
): string | null {
  const href = safeNotificationHref(item.href);
  // Reminders created before the direct-review route was added are durable
  // history. Upgrade their old library destination at click time.
  if (item.kind === "daily_cards_review" && href === "/decks") {
    return TODAY_REVIEW_HREF;
  }
  return href;
}

function notificationActionLabel(item: StudyNotification): string {
  return item.kind === "daily_cards_review" ? "Review today’s cards" : "Open";
}

function deliveredNotificationIds(): Set<string> {
  try {
    const value = window.localStorage.getItem(DELIVERED_STORAGE_KEY);
    const parsed = value ? (JSON.parse(value) as unknown) : [];
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((item): item is string => typeof item === "string")
        : [],
    );
  } catch {
    return new Set();
  }
}

function rememberDeliveredNotification(ids: Set<string>, id: string) {
  ids.add(id);
  try {
    window.localStorage.setItem(
      DELIVERED_STORAGE_KEY,
      JSON.stringify(Array.from(ids).slice(-200)),
    );
  } catch {
    // Private browsing or a full storage quota must not break in-app history.
  }
}

function createdLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recently";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function NotificationCenter() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState<StudyNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const loadSequence = useRef(0);
  const mutationPending = useRef(false);
  const locallyReadIds = useRef<Set<string>>(new Set());
  const locallyDismissedIds = useRef<Set<string>>(new Set());
  const locallyReadAllAt = useRef<number | null>(null);

  const load = useCallback(async () => {
    if (mutationPending.current) return;
    const sequence = ++loadSequence.current;
    try {
      const result = await apiFetch<NotificationListResponse>("/notifications");
      if (sequence !== loadSequence.current) return;
      let adjustedUnreadCount = result.unread_count;
      const visibleNotifications = result.notifications
        .filter((item) => {
          if (!locallyDismissedIds.current.has(item.notification_id)) {
            return true;
          }
          if (!item.read_at) adjustedUnreadCount -= 1;
          return false;
        })
        .map((item) => {
          const readAllAt = locallyReadAllAt.current;
          const locallyRead =
            locallyReadIds.current.has(item.notification_id) ||
            (readAllAt !== null && Date.parse(item.created_at) <= readAllAt);
          if (locallyRead && !item.read_at) {
            adjustedUnreadCount -= 1;
            return { ...item, read_at: new Date().toISOString() };
          }
          return item;
        });

      if (
        typeof window !== "undefined" &&
        "Notification" in window &&
        window.Notification.permission === "granted"
      ) {
        const delivered = deliveredNotificationIds();
        let enabledAt = browserAlertEnabledAt();
        if (enabledAt === null) {
          // Permission may have been granted before this feature existed. Its
          // old unread history belongs in the center, not in an alert storm.
          enabledAt = rememberBrowserAlertsEnabled();
        }
        for (const item of visibleNotifications) {
          if (item.read_at || delivered.has(item.notification_id)) continue;
          const createdAt = Date.parse(item.created_at);
          if (!Number.isFinite(createdAt) || createdAt < enabledAt) continue;
          try {
            const alert = new window.Notification(item.title, {
              body: item.body,
              tag: `mugensei:${item.notification_id}`,
            });
            rememberDeliveredNotification(delivered, item.notification_id);
            const href = notificationDestination(item);
            alert.onclick = () => {
              window.focus();
              loadSequence.current += 1;
              mutationPending.current = true;
              locallyReadIds.current.add(item.notification_id);
              void apiFetch<unknown>(
                `/notifications/${item.notification_id}/read`,
                { method: "POST" },
              )
                .catch(() => undefined)
                .finally(() => {
                  mutationPending.current = false;
                });
              setNotifications((current) =>
                current.map((entry) =>
                  entry.notification_id === item.notification_id
                    ? { ...entry, read_at: new Date().toISOString() }
                    : entry,
                ),
              );
              setUnreadCount((current) => Math.max(0, current - 1));
              if (href) router.push(href);
              alert.close();
            };
          } catch {
            // Native notification failure must never hide persistent history.
          }
        }
      }

      setNotifications(visibleNotifications);
      setUnreadCount(Math.max(0, adjustedUnreadCount));
      setError("");
    } catch (caught) {
      if (sequence !== loadSequence.current) return;
      setError(
        (caught as Error).message || "Could not load your notifications.",
      );
    } finally {
      if (sequence === loadSequence.current) setLoaded(true);
    }
  }, [router]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    const onFocus = () => void load();
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void load();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [load]);

  async function markRead(item: StudyNotification, navigate: boolean) {
    const href = notificationDestination(item);
    setPendingId(item.notification_id);
    loadSequence.current += 1;
    mutationPending.current = true;
    setError("");
    try {
      if (!item.read_at) {
        await apiFetch<unknown>(
          `/notifications/${item.notification_id}/read`,
          { method: "POST" },
        );
        locallyReadIds.current.add(item.notification_id);
        setNotifications((current) =>
          current.map((entry) =>
            entry.notification_id === item.notification_id
              ? { ...entry, read_at: new Date().toISOString() }
              : entry,
          ),
        );
        setUnreadCount((current) => Math.max(0, current - 1));
      }
      if (navigate && href) {
        setOpen(false);
        router.push(href);
      }
    } catch (caught) {
      setError(
        (caught as Error).message || "Could not update that notification.",
      );
    } finally {
      mutationPending.current = false;
      setPendingId(null);
    }
  }

  async function dismiss(item: StudyNotification) {
    setPendingId(item.notification_id);
    loadSequence.current += 1;
    mutationPending.current = true;
    setError("");
    try {
      await apiFetch<unknown>(
        `/notifications/${item.notification_id}/dismiss`,
        { method: "POST" },
      );
      locallyDismissedIds.current.add(item.notification_id);
      setNotifications((current) =>
        current.filter(
          (entry) => entry.notification_id !== item.notification_id,
        ),
      );
      if (!item.read_at) {
        setUnreadCount((current) => Math.max(0, current - 1));
      }
    } catch (caught) {
      setError(
        (caught as Error).message || "Could not dismiss that notification.",
      );
    } finally {
      mutationPending.current = false;
      setPendingId(null);
    }
  }

  async function markAllRead() {
    setPendingId("all");
    loadSequence.current += 1;
    mutationPending.current = true;
    setError("");
    try {
      await apiFetch<unknown>("/notifications/read-all", { method: "POST" });
      const now = new Date().toISOString();
      locallyReadAllAt.current = Date.parse(now);
      setNotifications((current) =>
        current.map((item) => ({ ...item, read_at: item.read_at ?? now })),
      );
      setUnreadCount(0);
    } catch (caught) {
      setError(
        (caught as Error).message || "Could not mark notifications as read.",
      );
    } finally {
      mutationPending.current = false;
      setPendingId(null);
    }
  }

  const triggerLabel =
    unreadCount > 0
      ? `Notifications, ${unreadCount} unread`
      : "Notifications";

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen) void load();
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="relative"
          aria-label={triggerLabel}
        >
          <Bell aria-hidden />
          {unreadCount > 0 ? (
            <span
              aria-hidden
              className="absolute -right-1 -top-1 grid min-w-4 place-items-center rounded-full bg-primary px-1 text-eyebrow font-semibold leading-4 text-primary-foreground"
            >
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-1rem))] p-0">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <h2 className="font-serif text-base font-medium">Notifications</h2>
          {unreadCount > 0 ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={pendingId !== null}
              onClick={() => void markAllRead()}
            >
              {pendingId === "all" ? (
                <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <CheckCheck aria-hidden />
              )}
              Mark all read
            </Button>
          ) : null}
        </div>

        <div className="max-h-[min(28rem,70vh)] overflow-y-auto">
          {!loaded ? (
            <p className="p-6 text-center text-sm text-muted-foreground" role="status">
              Loading notifications…
            </p>
          ) : notifications.length === 0 && error ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              Notifications are temporarily unavailable.
            </p>
          ) : notifications.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              You’re all caught up.
            </p>
          ) : (
            <ol className="divide-y divide-border">
              {notifications.map((item) => {
                const unread = !item.read_at;
                const href = notificationDestination(item);
                const actionLabel = href ? notificationActionLabel(item) : null;
                const busy = pendingId === item.notification_id;
                return (
                  <li
                    key={item.notification_id}
                    className={cn("flex items-start gap-2 p-3", unread && "bg-wash")}
                  >
                    <button
                      type="button"
                      aria-label={
                        actionLabel
                          ? `${actionLabel}: ${item.title}${unread ? ", unread" : ""}`
                          : `${unread ? "Mark notification as read" : "Notification"}: ${item.title}`
                      }
                      className="group -m-1 min-w-0 flex-1 cursor-pointer rounded-md p-1 text-left hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
                      disabled={pendingId !== null}
                      onClick={() => void markRead(item, Boolean(href))}
                    >
                      <span className="flex items-center gap-2 text-sm font-medium">
                        {unread ? (
                          <span
                            aria-label="Unread"
                            className="size-1.5 shrink-0 rounded-full bg-primary"
                          />
                        ) : null}
                        <span className="truncate">{item.title}</span>
                      </span>
                      <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                        {item.body}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {createdLabel(item.created_at)}
                      </span>
                      {actionLabel ? (
                        <span className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-primary group-hover:text-action-hover">
                          {actionLabel}
                          <ArrowRight aria-hidden className="size-4" />
                        </span>
                      ) : null}
                    </button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Dismiss notification: ${item.title}`}
                      disabled={pendingId !== null}
                      onClick={() => void dismiss(item)}
                    >
                      {busy ? (
                        <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" />
                      ) : (
                        <Trash2 aria-hidden />
                      )}
                    </Button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        {error ? (
          <div className="border-t border-border p-3">
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => void load()}
            >
              Try again
            </Button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
