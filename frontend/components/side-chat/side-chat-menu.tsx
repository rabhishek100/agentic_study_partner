"use client";

import { MessagesSquare, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { SideChatSummary } from "@/lib/types";

export interface SideChatMenuProps {
  sideChats: SideChatSummary[];
  /** Which are currently on screen, open or minimized. */
  openIds: Set<string>;
  onOpen: (sideChat: SideChatSummary) => void;
  onDelete: (sideChatId: string) => void;
}

/**
 * Every side chat of this conversation, including the ones not on screen.
 *
 * Closing a window is not deleting a thread — the question and its grounded
 * answer are recorded — so there has to be somewhere to get it back from. This
 * is also the only place a side chat can be deleted outright, which keeps a
 * destructive action out of the window's own title bar next to Close.
 */
export function SideChatMenu({
  sideChats,
  openIds,
  onOpen,
  onDelete,
}: SideChatMenuProps) {
  if (sideChats.length === 0) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="Side chats">
          <MessagesSquare aria-hidden />
          <span className="tabular-nums">{sideChats.length}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel className="font-normal text-muted-foreground">
          Side chats on this conversation
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {sideChats.map((sideChat) => (
          <DropdownMenuItem
            key={sideChat.conversation_id}
            className="flex items-start gap-2"
            onSelect={() => onOpen(sideChat)}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{sideChat.title}</span>
              <span className="block text-xs text-muted-foreground">
                {sideChat.turn_count === 0
                  ? "No questions yet"
                  : `${sideChat.turn_count} question${
                      sideChat.turn_count === 1 ? "" : "s"
                    }`}
                {openIds.has(sideChat.conversation_id) ? " · open" : ""}
              </span>
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Delete the ${sideChat.title} side chat`}
              onClick={(event) => {
                // The row itself opens the thread; deleting must not also open
                // the thing it is removing.
                event.stopPropagation();
                onDelete(sideChat.conversation_id);
              }}
            >
              <Trash2 aria-hidden />
            </Button>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
