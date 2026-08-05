"use client";

import { Check, MoreHorizontal, Pencil, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * The little a sidebar row needs to know about a conversation.
 *
 * Structural rather than the book's `ConversationSummary`, because a video
 * conversation is the same thing in a sidebar — a title, a turn count, and a
 * recency — and duplicating the rename and delete interactions to say so
 * would mean maintaining two of every state this row can be in.
 */
export interface HistoryEntry {
  conversation_id: string;
  title: string;
  turn_count: number;
  updated_at: string;
}

/** Coarse recency buckets; exact timestamps are noise in a sidebar. */
export function recencyGroup(updatedAt: string, now: Date): string {
  const updated = new Date(updatedAt);
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);

  if (updated >= startOfToday) return "Today";

  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfYesterday.getDate() - 1);
  if (updated >= startOfYesterday) return "Yesterday";

  const startOfWeek = new Date(startOfToday);
  startOfWeek.setDate(startOfWeek.getDate() - 7);
  if (updated >= startOfWeek) return "Previous 7 days";

  const startOfMonth = new Date(startOfToday);
  startOfMonth.setDate(startOfMonth.getDate() - 30);
  if (updated >= startOfMonth) return "Previous 30 days";

  return "Older";
}

export function groupByRecency(
  conversations: HistoryEntry[],
  now: Date,
): { label: string; items: HistoryEntry[] }[] {
  const groups = new Map<string, HistoryEntry[]>();
  for (const conversation of conversations) {
    const label = recencyGroup(conversation.updated_at, now);
    const existing = groups.get(label);
    if (existing) existing.push(conversation);
    else groups.set(label, [conversation]);
  }
  // Map preserves insertion order, and the list arrives newest-first, so the
  // groups come out in recency order without a second sort.
  return [...groups.entries()].map(([label, items]) => ({ label, items }));
}

function ConversationRow({
  conversation,
  isActive,
  onOpen,
  onRename,
  onDelete,
}: {
  conversation: HistoryEntry;
  isActive: boolean;
  onOpen: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(conversation.title);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  if (renaming) {
    return (
      <li className="flex items-center gap-1 px-1 py-0.5">
        <Input
          autoFocus
          value={draft}
          aria-label="Conversation title"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && draft.trim()) {
              onRename(draft.trim());
              setRenaming(false);
            }
            if (event.key === "Escape") {
              setDraft(conversation.title);
              setRenaming(false);
            }
          }}
        />
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="Save title"
          disabled={!draft.trim()}
          onClick={() => {
            onRename(draft.trim());
            setRenaming(false);
          }}
        >
          <Check aria-hidden />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="Cancel rename"
          onClick={() => {
            setDraft(conversation.title);
            setRenaming(false);
          }}
        >
          <X aria-hidden />
        </Button>
      </li>
    );
  }

  if (confirmingDelete) {
    return (
      <li className="space-y-1.5 rounded-md border border-destructive/40 px-2 py-2">
        <p className="text-xs">Delete “{conversation.title}”?</p>
        <div className="flex gap-1.5">
          <Button size="xs" variant="destructive" onClick={onDelete}>
            Delete
          </Button>
          <Button
            size="xs"
            variant="ghost"
            onClick={() => setConfirmingDelete(false)}
          >
            Cancel
          </Button>
        </div>
      </li>
    );
  }

  return (
    <li className="group/row relative">
      <button
        type="button"
        onClick={onOpen}
        aria-current={isActive ? "true" : undefined}
        className={cn(
          "flex w-full items-baseline gap-2 rounded-md px-2 py-1.5 pr-7 text-left text-sm transition-colors",
          isActive
            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
            : "hover:bg-sidebar-accent/60",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{conversation.title}</span>
        {conversation.turn_count > 0 && (
          <span className="shrink-0 text-[0.7rem] tabular-nums text-muted-foreground">
            {conversation.turn_count}
          </span>
        )}
      </button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={`Actions for ${conversation.title}`}
            className="absolute right-0.5 top-1 opacity-0 focus-visible:opacity-100 group-hover/row:opacity-100"
          >
            <MoreHorizontal aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => setRenaming(true)}>
            <Pencil aria-hidden />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => setConfirmingDelete(true)}
          >
            <Trash2 aria-hidden />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

export interface ConversationHistoryProps {
  conversations: HistoryEntry[];
  loaded: boolean;
  activeId: string | null;
  onOpen: (conversationId: string) => void;
  onRename: (conversationId: string, title: string) => void;
  onDelete: (conversationId: string) => void;
  onNew: () => void;
  /** Injected so grouping is testable without freezing the clock globally. */
  now?: Date;
}

export function ConversationHistory({
  conversations,
  loaded,
  activeId,
  onOpen,
  onRename,
  onDelete,
  onNew,
  now,
}: ConversationHistoryProps) {
  const groups = groupByRecency(conversations, now ?? new Date());

  return (
    <section aria-labelledby="history-heading" className="space-y-2">
      <div className="flex items-center justify-between">
        <h2
          id="history-heading"
          className="text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
        >
          Conversations
        </h2>
        <Button size="xs" variant="ghost" onClick={onNew}>
          <Plus aria-hidden />
          New
        </Button>
      </div>

      {!loaded ? (
        <div className="space-y-1.5" aria-hidden>
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-4/5" />
        </div>
      ) : conversations.length === 0 ? (
        <p className="px-2 text-xs text-muted-foreground">
          Nothing yet. Ask a question and it will be saved here.
        </p>
      ) : (
        <div className="space-y-3">
          {groups.map((group) => (
            <div key={group.label} className="space-y-0.5">
              <p className="px-2 text-[0.7rem] text-muted-foreground">
                {group.label}
              </p>
              <ul>
                {group.items.map((conversation) => (
                  <ConversationRow
                    key={conversation.conversation_id}
                    conversation={conversation}
                    isActive={conversation.conversation_id === activeId}
                    onOpen={() => onOpen(conversation.conversation_id)}
                    onRename={(title) =>
                      onRename(conversation.conversation_id, title)
                    }
                    onDelete={() => onDelete(conversation.conversation_id)}
                  />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
