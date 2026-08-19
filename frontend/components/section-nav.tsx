"use client";

import { BookOpen, FileText, Layers, MessagesSquare, Video } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * Books, Papers, Videos, and Cards are peers, not features hidden inside the reader.
 * The switcher lives in the header so none of them looks like a mode of
 * another.
 */
export function SectionNav({
  active,
}: {
  // "prompts" names a screen that is reachable from the sections but is not
  // one of them: nothing highlights, and the switcher still gets the reader
  // back to a library in one click.
  active: "books" | "papers" | "videos" | "decks" | "interviews" | "prompts";
}) {
  const sections = [
    { key: "books" as const, href: "/", label: "Books", icon: BookOpen },
    { key: "papers" as const, href: "/papers", label: "Papers", icon: FileText },
    { key: "videos" as const, href: "/videos", label: "Videos", icon: Video },
    {
      key: "interviews" as const,
      href: "/interviews",
      label: "Interview",
      icon: MessagesSquare,
    },
    { key: "decks" as const, href: "/decks", label: "Cards", icon: Layers },
  ];
  return (
    <nav aria-label="Library sections" className="flex items-center gap-1">
      {sections.map(({ key, href, label, icon: Icon }) => (
        <Link
          key={key}
          href={href}
          aria-current={active === key ? "page" : undefined}
          className={cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
            "",
            // Selection is the jade wash; hover is the neutral ground. They
            // were both `accent` before, so the current section and a hovered
            // one looked identical.
            active === key
              ? "bg-wash font-medium text-foreground"
              : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
          )}
        >
          <Icon aria-hidden className="size-4" />
          {label}
        </Link>
      ))}
    </nav>
  );
}
