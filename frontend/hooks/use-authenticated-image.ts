"use client";

import { useEffect, useState } from "react";

import { accessToken } from "@/lib/supabase";

type State =
  | { status: "loading"; url: null }
  | { status: "ready"; url: string }
  | { status: "failed"; url: null };

/**
 * Load an image from an endpoint that requires a bearer token.
 *
 * A plain `<img src>` cannot carry an `Authorization` header, and this API
 * authenticates only that way — so figures pointed straight at the endpoint
 * arrive unauthenticated and 401. Fetching with the token and handing the
 * element an object URL is what makes them load at all.
 *
 * HTTP caching still applies: the fetch goes through the browser cache, and
 * the endpoint's strong ETag and `immutable` response mean a revalidation is
 * answered 304 rather than re-sending the bytes.
 */
export function useAuthenticatedImage(source: string): State {
  const [state, setState] = useState<State>({ status: "loading", url: null });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    // An empty source is "nothing to load here", not a relative URL. Callers
    // decide per render whether an image exists — a lecture may have no
    // poster frame — and a hook cannot be called behind that condition, so
    // the condition arrives as the argument instead. Fetching "" would
    // request the current page and hand the element an HTML document.
    if (!source) {
      setState({ status: "failed", url: null });
      return;
    }

    (async () => {
      try {
        const token = await accessToken();
        const response = await fetch(source, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        // Do not remove the skeleton until the browser has decoded the image.
        // Swapping it for an undecoded blob produced a blank frame that was
        // especially visible while scrolling or moving a floating window.
        const decoded = new Image();
        decoded.src = objectUrl;
        await decoded.decode?.().catch(() => undefined);
        if (cancelled) return;
        setState({ status: "ready", url: objectUrl });
      } catch {
        if (!cancelled) setState({ status: "failed", url: null });
      }
    })();

    return () => {
      cancelled = true;
      // Object URLs pin the blob in memory until revoked; a long conversation
      // would otherwise accumulate every figure it ever rendered.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source]);

  return state;
}
