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
  HEADER_INSET,
} from "@/lib/floating-window";
import type { SideChatSurface, SideChatThread } from "@/lib/side-chat";
import type { Anchor } from "@/lib/types";

export interface SideChatWindow {
  sideChat: SideChatThread;
  rect: WindowRect;
  minimized: boolean;
  /** An answer landed while this window was minimized. */
  unread: boolean;
  /**
   * Whether this thread floats over the page or sits in the surface that owns
   * it.
   *
   * Ask-first surfaces have nowhere else to put a thread, so theirs are always
   * detached. Source-first reading has a questions panel, and a thread lives
   * there by default; detaching is the reader's explicit request for a second
   * answer on screen beside the first. The flag rides on the window rather than
   * on the surface because the two states are the same thread — the component
   * stays mounted across the change, so an answer keeps streaming while it
   * moves.
   */
  detached: boolean;
  /**
   * A question to ask as soon as this window is ready, asked once.
   *
   * Source-first study types the question *before* the window exists: the
   * reader is looking at a page, not at a thread, and the composer under the
   * document is where they ask. Carrying it here rather than opening an empty
   * window and expecting them to retype it is the whole difference between
   * asking about the page and asking beside it.
   */
  pendingQuestion?: string;
}

/**
 * What to anchor a new side chat to.
 *
 * A quote names a turn of the parent conversation; a page names a place in the
 * source. The server tells the two apart by `kind`, and defaults its absence to
 * a quote, so the existing call sites need no change.
 */
export type OpenSideChatRequest = { question?: string; title?: string } & (
  | { kind?: "answer_quote"; parentTurnIndex: number; quotedText: string }
  | { kind: "document_page"; bookId: number; page: number }
  | { kind: "document_passage"; bookId: number; page: number; selectedText: string }
  | { kind: "lecture_moment"; videoId: string; timestampMs: number }
  | { kind: "lecture_stretch"; videoId: string; startMs: number; endMs: number }
  // No anchor at all: a question about the conversation's scope that names no
  // passage. The reader asks one by dismissing the page or moment chip before
  // sending.
  | { kind: "unanchored" }
);

/** Mirrors `MAXIMUM_ANCHORS` on the server. */
export const MAXIMUM_ANCHORS = 5;

const OPEN_KEY_PREFIX = "side-chat:open:";

/**
 * The longest quote the server stores, mirroring `MAXIMUM_QUOTE_CHARS`.
 *
 * Anchoring a whole answer can exceed it — the longest recorded answer is over
 * 34,000 characters — so the client cuts the quote rather than sending a request
 * that can only be rejected. What is kept is the opening of the answer, which is
 * where its claim is stated; the rest of the turn still reaches the model as
 * surrounding context.
 */
export const MAXIMUM_QUOTE_CHARS = 16_000;

/**
 * The header's actual height, so windows clear it even when the reader has
 * enlarged text and it has grown past its design height.
 */
function measuredInset(): number {
  const header = document.querySelector("header");
  return header ? Math.round(header.getBoundingClientRect().height) : HEADER_INSET;
}
export function clampQuote(text: string): string {
  const trimmed = text.trim();
  if (trimmed.length <= MAXIMUM_QUOTE_CHARS) return trimmed;
  const clipped = trimmed.slice(0, MAXIMUM_QUOTE_CHARS - 1);
  const boundary = clipped.lastIndexOf(" ");
  return `${(boundary > MAXIMUM_QUOTE_CHARS / 2
    ? clipped.slice(0, boundary)
    : clipped
  ).trimEnd()}…`;
}

/**
 * The longest title the server stores, mirroring `CreateSideChatRequest`.
 *
 * A window opened from a selection is named after that selection, and a reader
 * who highlights a paragraph produces one far longer than this. Sending it
 * anyway is a 422 that reads "Request failed" — which is what happened, and
 * from the reader's side the highlight simply did nothing.
 */
export const MAXIMUM_TITLE_CHARS = 200;

/** A title the server will accept, or nothing rather than something invalid. */
export function clampTitle(title: string | undefined): string | undefined {
  const trimmed = (title ?? "").trim();
  if (!trimmed) return undefined;
  if (trimmed.length <= MAXIMUM_TITLE_CHARS) return trimmed;
  const clipped = trimmed.slice(0, MAXIMUM_TITLE_CHARS - 1);
  const boundary = clipped.lastIndexOf(" ");
  return `${(boundary > MAXIMUM_TITLE_CHARS / 2
    ? clipped.slice(0, boundary)
    : clipped
  ).trimEnd()}…`;
}

/** The anchors a new side chat is created with — none, or exactly one. */
function anchorPayloads(
  request: OpenSideChatRequest,
): Record<string, unknown>[] {
  return request.kind === "unanchored" ? [] : [anchorPayload(request)];
}

/** One anchor in the shape the server stores it. */
function anchorPayload(
  request: Exclude<OpenSideChatRequest, { kind: "unanchored" }>,
): Record<string, unknown> {
  if (request.kind === "document_page") {
    return {
      kind: "document_page",
      book_id: request.bookId,
      page: request.page,
    };
  }
  if (request.kind === "document_passage") {
    return {
      kind: "document_passage",
      book_id: request.bookId,
      page: request.page,
      selected_text: request.selectedText,
    };
  }
  if (request.kind === "lecture_moment") {
    return {
      kind: "lecture_moment",
      video_id: request.videoId,
      timestamp_ms: request.timestampMs,
    };
  }
  if (request.kind === "lecture_stretch") {
    return {
      kind: "lecture_stretch",
      video_id: request.videoId,
      start_ms: request.startMs,
      end_ms: request.endMs,
    };
  }
  return {
    kind: "answer_quote",
    parent_turn_index: request.parentTurnIndex,
    quoted_text: clampQuote(request.quotedText),
  };
}

interface StoredWindow {
  id: string;
  minimized: boolean;
  detached?: boolean;
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
              detached: (entry as StoredWindow).detached,
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
          detached: entry.detached,
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
  return {
    width: window.innerWidth,
    height: window.innerHeight,
    inset: measuredInset(),
  };
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
export function useSideChats(
  parentConversationId: string | null,
  surface: SideChatSurface,
  {
    /**
     * Where a newly shown thread goes.
     *
     * True on the ask-first surfaces, which have no panel to put a thread in.
     * False on reading and watching, where the questions panel is the thread's
     * home and floating is something the reader asks for.
     */
    detachedByDefault = true,
  }: { detachedByDefault?: boolean } = {},
) {
  const [available, setAvailable] = useState<SideChatThread[]>([]);
  const [windows, setWindows] = useState<SideChatWindow[]>([]);
  // Surfaced by the interface. A side chat that fails to open leaves nothing on
  // screen to attach a message to, so an unreported error here is a click that
  // silently does nothing — which is exactly what happened when the quote
  // length limit was too low.
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
    apiFetch<{ side_chats: SideChatThread[] }>(
      surface.list(parentConversationId),
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
              detached: entry.detached ?? detachedByDefault,
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
  }, [parentConversationId, surface]);

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

  const show = useCallback(
    (sideChat: SideChatThread, pendingQuestion?: string) => {
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
          { ...existing, sideChat, minimized: false, unread: false, pendingQuestion },
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
          detached: detachedByDefault,
          pendingQuestion,
        },
      ];
      });
    },
    [detachedByDefault],
  );

  /**
   * Forget a question once it has been asked.
   *
   * Sending is the window's job — it owns the stream — so the state that says
   * "ask this" has to be cleared from outside it, or a re-render would ask
   * again.
   */
  const clearPending = useCallback((sideChatId: string) => {
    setWindows((current) =>
      current.map((entry) =>
        entry.sideChat.conversation_id === sideChatId
          ? { ...entry, pendingQuestion: undefined }
          : entry,
      ),
    );
  }, []);

  const open = useCallback(
    async (request: OpenSideChatRequest) => {
      if (!parentConversationId) return null;
      setError("");
      setIsOpening(true);
      try {
        const created = await apiFetch<SideChatThread>(
          surface.create(parentConversationId),
          {
            method: "POST",
            body: JSON.stringify({
              anchors: anchorPayloads(request),
              ...(clampTitle(request.title)
                ? { title: clampTitle(request.title) }
                : {}),
            }),
          },
        );
        setAvailable((current) => [created, ...current]);
        show(created, request.question);
        return created;
      } catch (caught) {
        setError((caught as Error).message);
        return null;
      } finally {
        setIsOpening(false);
      }
    },
    [parentConversationId, show, surface],
  );

  const close = useCallback((sideChatId: string) => {
    setWindows((current) =>
      current.filter(
        (entry) => entry.sideChat.conversation_id !== sideChatId,
      ),
    );
  }, []);

  const remove = useCallback(
    async (sideChatId: string) => {
    setWindows((current) =>
      current.filter((entry) => entry.sideChat.conversation_id !== sideChatId),
    );
    setAvailable((current) =>
      current.filter((chat) => chat.conversation_id !== sideChatId),
    );
    forgetGeometry(sideChatId);
    try {
        await apiFetch<void>(surface.remove(sideChatId), {
          method: "DELETE",
        });
      } catch (caught) {
        setError((caught as Error).message);
      }
    },
    [surface],
  );

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

  /**
   * Pop a thread out of the surface that holds it, into a window of its own.
   *
   * The window it becomes is the same mounted component, so a detach in the
   * middle of an answer does not interrupt it. Raised as well as detached,
   * because a new window that opens behind the others is a window the reader
   * has to go looking for.
   */
  const detach = useCallback(
    (sideChatId: string) => {
      setWindows((current) =>
        current.map((entry) =>
          entry.sideChat.conversation_id === sideChatId
            ? { ...entry, detached: true, minimized: false, unread: false }
            : entry,
        ),
      );
      focus(sideChatId);
    },
    [focus],
  );

  /**
   * Put a detached thread back where it came from.
   *
   * This is what closing and minimising a detached window mean on a surface
   * that lists its threads: the thread is not going anywhere, so taking the
   * window away returns it to the list rather than ending it.
   */
  const attach = useCallback((sideChatId: string) => {
    setWindows((current) =>
      current.map((entry) =>
        entry.sideChat.conversation_id === sideChatId
          ? { ...entry, detached: false, minimized: false, unread: false }
          : entry,
      ),
    );
  }, []);

  /**
   * Replace the passages a side chat is anchored to.
   *
   * The whole set is sent, existing chips keeping their ids, because that is
   * the contract the endpoint offers and because the client always knows the
   * intended set — merging server-side would only add a way for two windows to
   * disagree about it.
   */
  const setAnchors = useCallback(
    async (sideChatId: string, anchors: Anchor[]) => {
      const previous = { available, windows };
      const apply = (chat: SideChatThread) =>
        chat.conversation_id === sideChatId ? { ...chat, anchors } : chat;
      // Applied first so removing a chip feels immediate, and rolled back if
      // the server refuses.
      setAvailable((current) => current.map(apply));
      setWindows((current) =>
        current.map((entry) =>
          entry.sideChat.conversation_id === sideChatId
            ? { ...entry, sideChat: { ...entry.sideChat, anchors } }
            : entry,
        ),
      );
      try {
        const updated = await apiFetch<SideChatThread>(
          surface.update(sideChatId),
          {
            method: "PATCH",
            body: JSON.stringify({
              // Each anchor goes back in the shape it came in. A source
              // anchor has no quote to clamp and no turn to name, so mapping
              // every anchor through the quote shape would send nulls for
              // both and be refused.
              anchors: anchors.map((anchor) =>
                anchor.kind === undefined || anchor.kind === "answer_quote"
                  ? {
                      kind: "answer_quote",
                      anchor_id: anchor.anchor_id,
                      parent_turn_index: anchor.parent_turn_index,
                      quoted_text: clampQuote(anchor.quoted_text),
                    }
                  : { ...anchor },
              ),
            }),
          },
        );
        setAvailable((current) =>
          current.map((chat) =>
            chat.conversation_id === sideChatId ? updated : chat,
          ),
        );
        setWindows((current) =>
          current.map((entry) =>
            entry.sideChat.conversation_id === sideChatId
              ? { ...entry, sideChat: updated }
              : entry,
          ),
        );
        return true;
      } catch (caught) {
        setError((caught as Error).message);
        setAvailable(previous.available);
        setWindows(previous.windows);
        return false;
      }
    },
    [available, windows, surface],
  );

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

  const detachedIds = useMemo(
    () =>
      new Set(
        windows
          .filter((entry) => entry.detached)
          .map((entry) => entry.sideChat.conversation_id),
      ),
    [windows],
  );

  const dismissError = useCallback(() => setError(""), []);

  return {
    available,
    windows,
    openIds,
    detachedIds,
    error,
    dismissError,
    isOpening,
    open,
    show,
    close,
    remove,
    setMinimized,
    setRect,
    setAnchors,
    noteSettled,
    focus,
    clearPending,
    detach,
    attach,
  };
}
