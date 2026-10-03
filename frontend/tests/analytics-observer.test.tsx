import { StrictMode } from "react";
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({ track: vi.fn(), init: vi.fn(), identify: vi.fn(), reset: vi.fn(), unsubscribe: vi.fn(), auth: undefined as undefined | ((event: string, session: { user: { id: string } } | null) => void) }));
vi.mock("next/navigation", () => ({ usePathname: () => "/read/private-book" }));
vi.mock("@/lib/analytics", async (importOriginal) => ({ ...await importOriginal<typeof import("@/lib/analytics")>(), initAnalytics: mock.init, track: mock.track, identifyAnalytics: mock.identify, resetAnalytics: mock.reset }));
vi.mock("@/lib/supabase", () => ({ supabase: { auth: { onAuthStateChange: (callback: typeof mock.auth) => { mock.auth = callback; return { data: { subscription: { unsubscribe: mock.unsubscribe } } }; } } } }));
import { AnalyticsObserver } from "@/components/analytics-observer";

describe("whole-app observer", () => {
  it("captures one safe action under Strict Mode, handles auth, and cleans up", () => {
    window.history.replaceState({}, "", "/read/private-book");
    const view = render(<StrictMode><AnalyticsObserver /><button aria-label="secret question">private book filename</button><a onClick={(e) => e.preventDefault()} href="/interviews/private-id?token=secret">private answer</a></StrictMode>);
    expect(mock.track.mock.calls.filter(([event]) => event === "$pageview")).toHaveLength(1);
    mock.track.mockClear();
    fireEvent.click(view.getByRole("button"));
    expect(mock.track).toHaveBeenCalledTimes(1);
    expect(mock.track).toHaveBeenCalledWith("ui_action", { route: "/read/:id", control: "button", action: "button" });
    fireEvent.click(view.getByRole("link"));
    expect(JSON.stringify(mock.track.mock.calls)).not.toMatch(/private|secret|filename|answer/);
    mock.auth?.("SIGNED_IN", { user: { id: "opaque-user" } });
    mock.auth?.("SIGNED_IN", { user: { id: "opaque-user" } });
    expect(mock.identify).toHaveBeenCalledTimes(1);
    mock.auth?.("SIGNED_OUT", null);
    expect(mock.reset).toHaveBeenCalledTimes(1);
    view.unmount();
    window.history.replaceState({}, "", "/");
    expect(mock.unsubscribe).toHaveBeenCalled();
    mock.track.mockClear();
    document.body.click();
    expect(mock.track).not.toHaveBeenCalled();
  });
});
