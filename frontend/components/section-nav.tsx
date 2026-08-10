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
  active: "books" | "papers" | "videos" | "decks" | "interviews";
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
            "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            active === key
              ? "bg-accent font-medium text-accent-foreground"
              : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
          )}
        >
          <Icon aria-hidden className="size-4" />
          {label}
        </Link>
      ))}
    </nav>
  );
}
