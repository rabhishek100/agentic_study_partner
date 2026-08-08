/**
 * What a side chat needs to know about the conversation it hangs off.
 *
 * Books and lectures answer through different endpoints and render answers with
 * different components — a lecture answer seeks a player and opens slides — but
 * the window, the queue, the geometry and the anchors are the same in both. So
 * the surface is a parameter: the paths to talk to, and (at the component
 * boundary) how to draw a turn.
 */

import type { QuoteAnchor } from "@/lib/types";

/** The fields the window chrome and the thread list need from any side chat. */
export interface SideChatThread {
  conversation_id: string;
  parent_conversation_id: string;
  title: string;
  anchors: QuoteAnchor[];
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export interface SideChatSurface {
  /** Distinguishes the two surfaces in remembered state and in test output. */
  kind: "book" | "video";
  /**
   * Whether the turn endpoint accepts an answer depth.
   *
   * The book chat has three depths; the lecture chat has none, and its request
   * contract forbids unknown fields — so sending one anyway produced a
   * validation error on every lecture side turn. The window hides the control
   * when it would not be honoured.
   */
  supportsDepth: boolean;
  /** Side chats of one parent conversation. */
  list(parentConversationId: string): string;
  create(parentConversationId: string): string;
  update(sideChatId: string): string;
  /** The SSE turn endpoint. */
  stream(sideChatId: string): string;
  /** Turn history, for a window reopened after it was closed. */
  detail(sideChatId: string): string;
  /** Deleting a side chat is deleting a conversation. */
  remove(sideChatId: string): string;
}

export const BOOK_SIDE_CHATS: SideChatSurface = {
  kind: "book",
  supportsDepth: true,
  list: (parent) => `/conversations/${parent}/side-chats`,
  create: (parent) => `/conversations/${parent}/side-chats`,
  update: (id) => `/side-chats/${id}`,
  stream: (id) => `/side-chats/${id}/turns/stream`,
  detail: (id) => `/conversations/${id}`,
  remove: (id) => `/conversations/${id}`,
};

export const VIDEO_SIDE_CHATS: SideChatSurface = {
  kind: "video",
  supportsDepth: false,
  list: (parent) => `/video-conversations/${parent}/side-chats`,
  create: (parent) => `/video-conversations/${parent}/side-chats`,
  update: (id) => `/video-side-chats/${id}`,
  stream: (id) => `/video-side-chats/${id}/turns/stream`,
  detail: (id) => `/video-conversations/${id}`,
  remove: (id) => `/video-conversations/${id}`,
};

/** One exchange inside a side chat, whatever the surface's result type is. */
export interface SideChatTurn<TResult> {
  id: string;
  question: string;
  answer: string;
  status: "streaming" | "complete" | "stopped" | "failed";
  result: TResult | null;
  error: string | null;
}

/** The turn shape both conversation-detail endpoints return. */
export interface StoredSideChatTurn<TResult> {
  turn_index: number;
  question: string;
  answer: string | null;
  result: TResult | null;
}
