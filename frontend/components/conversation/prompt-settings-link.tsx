"use client";

import { Settings2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import type { ResponseDepth } from "@/lib/types";

/**
 * The way into the prompt studio.
 *
 * This was a `Sheet` holding the whole editor: five prompts, the locked rules,
 * and two compiled previews in a drawer, over an overlay that paints the app
 * out. The editor is a page now (`/prompts`), so the composer keeps a control
 * of the size the control deserves — a link — and carries the two things the
 * studio needs to open in context: which conversation to apply to, and which
 * depth to compile the preview against.
 */
export function PromptSettingsLink({
  conversationId,
  responseDepth,
}: {
  conversationId: string | null;
  responseDepth: ResponseDepth;
}) {
  const pathname = usePathname();
  const params = new URLSearchParams({ depth: responseDepth, from: pathname });
  if (conversationId) params.set("conversation", conversationId);

  return (
    <Button type="button" variant="ghost" size="xs" asChild>
      <Link href={`/prompts?${params.toString()}`}>
        <Settings2 aria-hidden />
        Prompt settings
      </Link>
    </Button>
  );
}
