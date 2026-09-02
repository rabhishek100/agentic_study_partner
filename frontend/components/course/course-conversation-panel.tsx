"use client";

import { MessageSquarePlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { CourseConversationSummary } from "@/lib/course-types";
import { cn } from "@/lib/utils";

export function CourseConversationPanel({
  conversations,
  activeId,
  streaming,
  onNew,
  onOpen,
}: {
  conversations: CourseConversationSummary[];
  activeId: string | null;
  streaming: boolean;
  onNew(): void;
  onOpen(id: string): void;
}) {
  return (
    <aside
      aria-labelledby="course-conversations-title"
      className="hidden min-h-0 flex-col border-l border-divider bg-surface xl:flex"
    >
      <div className="space-y-3 border-b border-divider p-4">
        <h2 id="course-conversations-title" className="font-medium">Conversations</h2>
        <Button variant="ghost" className="w-full justify-start" onClick={onNew}>
          <MessageSquarePlus aria-hidden />
          New conversation
        </Button>
      </div>
      <nav aria-label="Course conversations" className="min-h-0 flex-1 space-y-1 overflow-y-auto p-3">
        {conversations.length === 0 ? (
          <p className="px-2 py-4 text-xs text-muted-foreground">No questions yet.</p>
        ) : (
          conversations.map((item) => (
            <button
              key={item.conversation_id}
              type="button"
              disabled={streaming}
              onClick={() => onOpen(item.conversation_id)}
              aria-current={activeId === item.conversation_id ? "page" : undefined}
              className={cn(
                "relative w-full rounded-md px-3 py-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                activeId === item.conversation_id
                  ? "bg-wash font-medium before:absolute before:inset-y-2 before:left-0 before:w-1 before:rounded-full before:bg-action"
                  : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
              )}
            >
              <span className="line-clamp-2 text-sm leading-snug">{item.title}</span>
              <span className="mt-1 block text-xs font-normal text-muted-foreground">
                {item.turn_count} turn{item.turn_count === 1 ? "" : "s"} · {item.selected_video_ids.length} lectures
              </span>
            </button>
          ))
        )}
      </nav>
    </aside>
  );
}
