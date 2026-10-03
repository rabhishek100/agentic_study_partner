"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { initAnalytics, identifyAnalytics, resetAnalytics, safeRoute, track } from "@/lib/analytics";
import { supabase } from "@/lib/supabase";

let lastPage: string | undefined;

export function AnalyticsObserver() {
  const pathname = usePathname();
  useEffect(() => {
    initAnalytics();
    // Deduplicate React Strict Mode effects; omit IDs and query strings.
    if (pathname !== lastPage) {
      lastPage = pathname;
      track("$pageview", { route: safeRoute(pathname || "/") });
    }
  }, [pathname]);

  useEffect(() => {
    const click = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      const control = event.target.closest("button, a, [role=tab], [role=menuitem], [data-analytics-action]");
      if (!control || control.hasAttribute("disabled")) return;
      const action = control.getAttribute("data-analytics-action");
      const link = control instanceof HTMLAnchorElement ? control.getAttribute("href") : null;
      // No DOM text, aria-label, input values or CSS/element IDs are captured.
      track("ui_action", {
        route: safeRoute(window.location.pathname),
        control: control.tagName.toLowerCase(),
        action: action || (link ? `navigate:${safeRoute(link)}` : control.getAttribute("data-slot") || control.getAttribute("role") || "button"),
      });
    };
    document.addEventListener("click", click);
    let identity: string | null = null;
    const subscription = supabase?.auth.onAuthStateChange((event, session) => {
      const next = session?.user.id || null;
      if (next && identity !== next) {
        identifyAnalytics(next); // opaque auth ID only; no email or profile
        if (event === "SIGNED_IN") track("signed_in");
      }
      if (event === "SIGNED_OUT") { track("signed_out"); resetAnalytics(); }
      identity = next;
    }).data.subscription;
    return () => { document.removeEventListener("click", click); subscription?.unsubscribe(); };
  }, []);
  return null;
}
