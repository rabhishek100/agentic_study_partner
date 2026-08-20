"use client";

import { useCallback, useEffect, useState } from "react";

const KEY_PREFIX = "asp:stay-in-source:";

/**
 * The **stay in this source** lock, remembered per session.
 *
 * Kept on the client rather than on the conversation. The lock is a standing
 * instruction about how the reader wants questions answered, not a fact about
 * the session — a second reader of the same book would want their own answer,
 * and a reader who changes their mind mid-session should not be writing to the
 * database to do it. It is sent with every turn, so the server never has to
 * guess what the current instruction is.
 */
export function useStayInSource(
  sessionId: string | null,
): [boolean, (locked: boolean) => void] {
  const [locked, setLocked] = useState(false);

  // Read after mount rather than during render: the server has no
  // localStorage, and seeding state from it would hydrate against a different
  // value.
  useEffect(() => {
    if (!sessionId) {
      setLocked(false);
      return;
    }
    setLocked(window.localStorage.getItem(`${KEY_PREFIX}${sessionId}`) === "true");
  }, [sessionId]);

  const update = useCallback(
    (next: boolean) => {
      setLocked(next);
      if (!sessionId) return;
      window.localStorage.setItem(`${KEY_PREFIX}${sessionId}`, String(next));
    },
    [sessionId],
  );

  return [locked, update];
}
