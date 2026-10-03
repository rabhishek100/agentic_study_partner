import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const { capture, init, identify, reset } = vi.hoisted(() => ({ capture: vi.fn(), init: vi.fn(), identify: vi.fn(), reset: vi.fn() }));
vi.mock("posthog-js", () => ({ default: { init, capture, identify, reset } }));
beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks(); capture.mockReset();
  vi.stubEnv("NEXT_PUBLIC_ANALYTICS_ENABLED", "true");
  vi.stubEnv("NEXT_PUBLIC_POSTHOG_KEY", "synthetic");
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
describe("analytics", () => {
  it("is disabled without explicit configuration", async () => {
    vi.stubEnv("NEXT_PUBLIC_ANALYTICS_ENABLED", "false");
    const analytics = await import("@/lib/analytics");
    analytics.initAnalytics(); analytics.track("ui_action");
    expect(init).not.toHaveBeenCalled(); expect(capture).not.toHaveBeenCalled();
  });
  it("redacts routes and automatically enriched properties before export", async () => {
    const analytics = await import("@/lib/analytics");
    expect(analytics.safeRoute("/read/private-book?question=secret")).toBe("/read/:id");
    expect(analytics.safeRoute("/api/books/private/chapter-name?token=secret", true)).toBe("/api/books/:id/:id");
    analytics.initAnalytics(); const config = init.mock.calls[0]![1];
    expect(config.disable_session_recording).toBe(true); expect(config.autocapture).toBe(false);
    const event = config.before_send({ event: "ui_action", $set: { email: "private" }, $set_once: { $initial_referrer: "private" }, $unset: ["email"], properties: { token: "caller-token", distinct_id: "opaque-user", route: "/read/:id", question: "private", $current_url: "https://app/read/secret?token=key", $referrer: "secret", $title: "private", $set: { email: "secret" } } });
    expect(event.properties).toEqual({ token: "synthetic", distinct_id: "opaque-user", route: "/read/:id", $current_url: "/read/:id", $ip: "0.0.0.0", $geoip_disable: true });
    expect(event.$set).toBeUndefined(); expect(event.$set_once).toBeUndefined(); expect(event.$unset).toBeUndefined();
    expect(config.before_send({ event: "$snapshot", properties: { data: "private" } })).toBeNull();
  });
  it("measures headers without consuming streams or exporting bodies", async () => {
    const analytics = await import("@/lib/analytics"); analytics.initAnalytics();
    const response = new Response("event: final\ndata: private answer\n\n", { headers: { "x-trace-id": "ops-id", "x-langsmith-trace-id": "ai-id" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const result = await analytics.trackedFetch("/api/chat/stream?secret=value", { method: "POST", body: "private prompt" });
    expect(result).toBe(response); expect(result.bodyUsed).toBe(false);
    expect(capture.mock.calls[1]![1]).toMatchObject({ route: "/api/chat/stream", trace_id: "ops-id", langsmith_trace_id: "ai-id", status: 200 });
    expect(JSON.stringify(capture.mock.calls)).not.toMatch(/private|secret|prompt/);
  });
  it("does not count GET polling and propagates network errors", async () => {
    const analytics = await import("@/lib/analytics"); analytics.initAnalytics();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
    await analytics.trackedFetch("/api/jobs/private"); expect(capture).not.toHaveBeenCalled();
    const failure = new Error("private error text"); vi.stubGlobal("fetch", vi.fn().mockRejectedValue(failure));
    await expect(analytics.trackedFetch("/api/revision-sheets", { method: "POST" })).rejects.toBe(failure);
    expect(capture.mock.calls[1]![0]).toBe("api_action_failed"); expect(JSON.stringify(capture.mock.calls)).not.toContain("private");
  });
  it("ignores analytics failures and excludes unknown properties", async () => {
    const analytics = await import("@/lib/analytics"); analytics.initAnalytics();
    capture.mockImplementation(() => { throw new Error("down"); });
    expect(() => analytics.track("ui_action", { action: "auth_submit", password: "private" })).not.toThrow();
    expect(capture.mock.calls[0]![1]).toEqual({ action: "auth_submit" });
  });
});
