"use client";

import posthog from "posthog-js";

const events = new Set(["$pageview", "$identify", "ui_action", "api_action_started", "api_action_response", "api_action_failed", "study_answer_completed", "study_answer_failed", "signed_in", "signed_out", "upload_completed", "upload_failed"]);
const propertyKeys = new Set(["route", "action", "control", "method", "status", "duration_ms", "trace_id", "langsmith_trace_id", "flow", "outcome", "distinct_id", "$anon_distinct_id", "$geoip_disable", "$session_id", "$window_id", "$lib", "$lib_version", "$insert_id", "$device_id", "$is_identified", "$process_person_profile", "$current_url", "$ip"]);
const pages = new Set(["videos", "watch", "courses", "read", "interviews", "ideal", "decks", "papers", "prompts"]);
// Only fixed API vocabulary can enter a path. IDs, filenames, queries and
// arbitrary user values become :id. Extend this list for new API resources.
const segments = new Set([...pages, "api", "books", "conversations", "video-conversations", "course-conversations", "revision-sheets", "reading-sessions", "watch-sessions", "notifications", "chat", "stream", "turns", "sections", "chapters", "summary", "summaries", "side-chats", "anchors", "jobs", "uploads", "upload", "captions", "resources", "transcriptions", "narration", "speech", "figures", "ideal-interviews", "voice-connection", "screen-checkpoints", "cards", "review", "reviews", "start", "finish", "attempts", "answers", "questions", "generate", "cancel", "retry", "pdf", "feedback", "preferences", "source-preferences", "marks", "messages", "checkpoints", "state", "continue", "complete", "resume", "source", "sources", "media", "health", "queue-health"]);
let ready = false;

export function safeRoute(input: string, api = false): string {
  try {
    const path = new URL(input, "https://local.invalid").pathname;
    const parts = path.split("/").filter(Boolean);
    if (api) {
      if (parts[0] !== "api") return "/external";
      return "/" + parts.map((part) => segments.has(part) ? part : ":id").join("/");
    }
    return "/" + parts.map((part, index) => (index === 0 && pages.has(part)) || (index === 1 && part === "ideal") ? part : ":id").join("/");
  } catch {
    return "/unknown";
  }
}

export function initAnalytics() {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (ready || !key || process.env.NEXT_PUBLIC_ANALYTICS_ENABLED !== "true") return;
  try {
    posthog.init(key, {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com",
      autocapture: false,
      capture_pageview: false,
      capture_pageleave: false,
      capture_dead_clicks: false,
      capture_heatmaps: false,
      capture_performance: false,
      capture_exceptions: false,
      disable_session_recording: true,
      disable_surveys: true,
      advanced_disable_flags: true,
      person_profiles: "identified_only",
      persistence: "localStorage",
      respect_dnt: true,
      rate_limiting: { events_per_second: 5, events_burst_limit: 10 },
      before_send: (event) => {
        if (!event || !events.has(event.event)) return null;
        // Remove automatic URL/referrer/title/geo fields too; a prompt or a
        // source filename can appear in these even if custom events are safe.
        event.properties = Object.fromEntries(Object.entries(event.properties).filter(([k]) => propertyKeys.has(k)));
        // The SDK places its public ingestion token in properties before this
        // hook. Restore it from configuration; never accept a caller's token.
        event.properties.token = key;
        // SDK enrichment also puts initial referrer/profile fields outside
        // properties. Those fields must pass the same privacy boundary.
        delete event.$set;
        delete event.$set_once;
        delete event.$unset;
        if (event.properties.$current_url) event.properties.$current_url = safeRoute(String(event.properties.$current_url));
        event.properties.$ip = "0.0.0.0";
        event.properties.$geoip_disable = true;
        return event;
      },
    });
    ready = true;
  } catch {
    // Analytics must never interrupt studying or sign-in.
  }
}

export function track(event: string, properties: Record<string, string | number | boolean> = {}) {
  if (!ready || !events.has(event)) return;
  try {
    posthog.capture(event, Object.fromEntries(Object.entries(properties).filter(([k]) => propertyKeys.has(k))));
  } catch { /* fail open */ }
}

export function identifyAnalytics(userId: string) {
  if (!ready) return;
  try { posthog.identify(userId); } catch { /* fail open */ }
}

export function resetAnalytics() {
  if (!ready) return;
  try { posthog.reset(); } catch { /* fail open */ }
}

/** Same fetch behavior; measures mutating API responses, not stream completion. */
export async function trackedFetch(input: string, options: RequestInit = {}): Promise<Response> {
  const method = (options.method || "GET").toUpperCase();
  const route = safeRoute(input, true);
  const measure = ready && method !== "GET" && route !== "/external";
  const started = performance.now();
  if (measure) track("api_action_started", { route, method });
  try {
    const response = await fetch(input, options);
    if (measure) track("api_action_response", {
      route, method, status: response.status, duration_ms: Math.round(performance.now() - started),
      langsmith_trace_id: response.headers.get("x-langsmith-trace-id") || "",
      trace_id: response.headers.get("x-trace-id") || "",
    });
    return response;
  } catch (error) {
    if (measure) track("api_action_failed", { route, method, outcome: error instanceof DOMException && error.name === "AbortError" ? "cancelled" : "failed" });
    throw error;
  }
}
