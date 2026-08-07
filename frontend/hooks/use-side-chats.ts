"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import {
  DEFAULT_HEIGHT,
  DEFAULT_WIDTH,
  cascadePlacement,
  clampRect,
  forgetGeometry,
  readGeometry,
  writeGeometry,
  type Viewport,
  type WindowRect,
} from "@/lib/floating-window";
import type {
  SideChatListResponse,
  SideChatSummary,
} from "@/lib/types";

export interface SideChatWindow {
  sideChat: SideChatSummary;
  rect: WindowRect;
  minimized: boolean;
  /** An answer landed while this window was minimized. */
  unread: boolean;
}

export interface OpenSideChatRequest {
  parentTurnIndex: number;
  quotedText: string;
}

const OPEN_KEY_PREFIX = "side-chat:open:";

interface StoredWindow {
  id: string;
  minimized: boolean;
}

function readOpen(parentId: string): StoredWindow[] {
  try {
    const raw = window.localStorage.getItem(`${OPEN_KEY_PREFIX}${parentId}`);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((entry) =>
      entry && typeof (entry as StoredWindow).id === "string"
        ? [
            {
              id: (entry as StoredWindow).id,
              minimized: Boolean((entry as StoredWindow).minimized),
            },
          ]
        : [],
    );
  } catch {
    return [];
  }
}

function writeOpen(parentId: string, windows: SideChatWindow[]): void {
  try {
    window.localStorage.setItem(
      `${OPEN_KEY_PREFIX}${parentId}`,
      JSON.stringify(
        windows.map((entry) => ({
          id: entry.sideChat.conversation_id,
          minimized: entry.minimized,
        })),
      ),
    );
  } catch {
    // Losing which windows were open is a smaller failure than refusing to
    // open one, so this stays silent.
  }
}

function viewportSize(): Viewport {
  if (typeof window === "undefined") return { width: 1280, height: 800 };
  return { width: window.innerWidth, height: window.innerHeight };
}

/**
 * Owns the side chats of one conversation: which exist, which are on screen,
 * where each one sits, and which is on top.
 *
 * Window order *is* stacking order — the last entry renders highest — so
 * focusing a window is moving it to the end of the list. Geometry is
 * remembered per side chat rather than per position on screen, so reopening a
 * thread puts it back where the reader last had it.
 *
 * A closed window is not a deleted thread. Closing takes it off screen and
 * leaves it listed, because the reader's question and its answer are recorded
 * server-side and are worth coming back to.
 */
export function useSideChats(parentConversationId: string | null) {
  const [available, setAvailable] = useState<SideChatSummary[]>([]);
  const [windows, setWindows] = useState<SideChatWindow[]>([]);
  const [error, setError] = useState("");
  const [isOpening, setIsOpening] = useState(false);
  // Restoration must not immediately overwrite what it just restored.
  const restoredRef = useRef<string | null>(null);

  useEffect(() => {
    setWindows([]);
    setAvailable([]);
    setError("");
    restoredRef.current = null;
    if (!parentConversationId) return;

    let cancelled = false;
    apiFetch<SideChatListResponse>(
      `/conversations/${parentConversationId}/side-chats`,
    )
      .then(({ side_chats: sideChats }) => {
        if (cancelled) return;
        setAvailable(sideChats);
        const stored = readOpen(parentConversationId);
        const byId = new Map(
          sideChats.map((chat) => [chat.conversation_id, chat]),
        );
        const viewport = viewportSize();
        const restored = stored.flatMap((entry, index) => {
          const sideChat = byId.get(entry.id);
          if (!sideChat) return [];
          return [
            {
              sideChat,
              rect: clampRect(
                readGeometry(entry.id) ?? cascadePlacement(index, viewport),
                viewport,
              ),
              minimized: entry.minimized,
              unread: false,
            },
          ];
        });
        restoredRef.current = parentConversationId;
        setWindows(restored);
      })
      .catch((caught) => {
        if (!cancelled) setError((caught as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [parentConversationId]);

  // Persist only after restoration has run for this conversation, so an
  // in-flight load cannot be recorded as "nothing was open".
  useEffect(() => {
    if (!parentConversationId) return;
    if (restoredRef.current !== parentConversationId) return;
    writeOpen(parentConversationId, windows);
  }, [parentConversationId, windows]);

  // A shrinking viewport must not strand a window out of reach.
  useEffect(() => {
    const onResize = () => {
      const viewport = viewportSize();
      setWindows((current) =>
        current.map((entry) => ({
          ...entry,
          rect: clampRect(entry.rect, viewport),
        })),
      );
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const focus = useCallback((sideChatId: string) => {
    setWindows((current) => {
      const index = current.findIndex(
        (entry) => entry.sideChat.conversation_id === sideChatId,
      );
      if (index < 0 || index === current.length - 1) return current;
      const next = [...current];
      const [moved] = next.splice(index, 1);
      if (!moved) return current;
      next.push(moved);
      return next;
    });
  }, []);

  const show = useCallback((sideChat: SideChatSummary) => {
    setWindows((current) => {
      const existing = current.find(
        (entry) => entry.sideChat.conversation_id === sideChat.conversation_id,
      );
      const viewport = viewportSize();
      if (existing) {
        // Already on screen: raise and restore it rather than opening a second
        // window onto the same thread.
        return [
          ...current.filter((entry) => entry !== existing),
          { ...existing, sideChat, minimized: false, unread: false },
        ];
      }
      return [
        ...current,
        {
          sideChat,
          rect: clampRect(
            readGeometry(sideChat.conversation_id) ?? {
              ...cascadePlacement(current.length, viewport),
              width: DEFAULT_WIDTH,
              height: DEFAULT_HEIGHT,
            },
            viewport,
          ),
          minimized: false,
          unread: false,
        },
      ];
    });
  }, []);

  const open = useCallback(
    async (request: OpenSideChatRequest) => {
      if (!parentConversationId) return null;
      setError("");
      setIsOpening(true);
      try {
        const created = await apiFetch<SideChatSummary>(
          `/conversations/${parentConversationId}/side-chats`,
          {
            method: "POST",
            body: JSON.stringify({
              anchors: [
                {
                  parent_turn_index: request.parentTurnIndex,
                  quoted_text: request.quotedText,
                },
              ],
            }),
          },
        );
        setAvailable((current) => [created, ...current]);
        show(created);
        return created;
      } catch (caught) {
        setError((caught as Error).message);
        return null;
      } finally {
        setIsOpening(false);
      }
    },
    [parentConversationId, show],
  );

  const close = useCallback((sideChatId: string) => {
    setWindows((current) =>
      current.filter(
        (entry) => entry.sideChat.conversation_id !== sideChatId,
      ),
    );
  }, []);

  const remove = useCallback(async (sideChatId: string) => {
    setWindows((current) =>
      current.filter((entry) => entry.sideChat.conversation_id !== sideChatId),
    );
    setAvailable((current) =>
      current.filter((chat) => chat.conversation_id !== sideChatId),
    );
    forgetGeometry(sideChatId);
    try {
      await apiFetch<void>(`/conversations/${sideChatId}`, {
        method: "DELETE",
      });
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, []);

  const setMinimized = useCallback((sideChatId: string, minimized: boolean) => {
    setWindows((current) =>
      current.map((entry) =>
        entry.sideChat.conversation_id === sideChatId
          ? { ...entry, minimized, unread: minimized ? entry.unread : false }
          : entry,
      ),
    );
    if (!minimized) focus(sideChatId);
  }, [focus]);

  const setRect = useCallback((sideChatId: string, rect: WindowRect) => {
    setWindows((current) =>
      current.map((entry) =>
        entry.sideChat.conversation_id === sideChatId
          ? { ...entry, rect }
          : entry,
      ),
    );
    writeGeometry(sideChatId, rect);
  }, []);

  /**
   * Called when a turn settles.
   *
   * Two things follow. A minimized window has to be able to say that its
   * answer landed, and the thread's turn count has to stop being the count
   * from page load — otherwise a thread the reader has just used still reads
   * "No questions yet" in the list. `recorded` is false for a turn that failed
   * or was stopped, which the server never stored.
   */
  const noteSettled = useCallback((sideChatId: string, recorded: boolean) => {
    setWindows((current) =>
      current.map((entry) => {
        if (entry.sideChat.conversation_id !== sideChatId) return entry;
        return {
          ...entry,
          unread: entry.minimized ? true : entry.unread,
          sideChat: recorded
            ? { ...entry.sideChat, turn_count: entry.sideChat.turn_count + 1 }
            : entry.sideChat,
        };
      }),
    );
    if (!recorded) return;
    setAvailable((current) =>
      current.map((chat) =>
        chat.conversation_id === sideChatId
          ? { ...chat, turn_count: chat.turn_count + 1 }
          : chat,
      ),
    );
  }, []);

  const openIds = useMemo(
    () => new Set(windows.map((entry) => entry.sideChat.conversation_id)),
    [windows],
  );

  return {
    available,
    windows,
    openIds,
    error,
    isOpening,
    open,
    show,
    close,
    remove,
    setMinimized,
    setRect,
    noteSettled,
    focus,
  };
}
