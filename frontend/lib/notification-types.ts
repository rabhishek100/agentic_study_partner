export interface StudyNotification {
  notification_id: string;
  kind: string;
  title: string;
  body: string;
  href: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
  dismissed_at?: string | null;
}

export interface NotificationListResponse {
  notifications: StudyNotification[];
  unread_count: number;
}

export interface DeckReminderPreferences {
  enabled: boolean;
  /** Local wall-clock time in HH:MM form. */
  reminder_time: string;
  /** IANA timezone such as Asia/Kolkata. */
  timezone: string;
  next_reminder_at?: string | null;
}

export type BrowserNotificationState =
  | "unsupported"
  | NotificationPermission;

export const BROWSER_ALERT_ENABLED_AT_KEY =
  "mugensei:browser-notifications-enabled-at";

export function browserNotificationState(): BrowserNotificationState {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "unsupported";
  }
  return window.Notification.permission;
}

/** Browser permission prompts must only be called from an explicit user action. */
export async function requestBrowserNotificationPermission(): Promise<BrowserNotificationState> {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "unsupported";
  }
  return window.Notification.requestPermission();
}

export function browserAlertEnabledAt(): number | null {
  if (typeof window === "undefined") return null;
  let value: string | null = null;
  try {
    value = window.localStorage.getItem(BROWSER_ALERT_ENABLED_AT_KEY);
  } catch {
    return null;
  }
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function rememberBrowserAlertsEnabled(now = new Date()): number {
  const timestamp = now.getTime();
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(
        BROWSER_ALERT_ENABLED_AT_KEY,
        now.toISOString(),
      );
    } catch {
      // Native alerts can still work for this open tab without persistence.
    }
  }
  return timestamp;
}
