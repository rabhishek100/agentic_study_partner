"use client";

import {
  BookOpen,
  FileText,
  GraduationCap,
  Layers,
  MessagesSquare,
  Video,
} from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * "prompts" names a screen that is reachable from the sections but is not one
 * of them: nothing highlights, and the switcher still gets the reader back to a
 * library in one click.
 */
export type SectionKey =
  | "books"
  | "papers"
  | "videos"
  | "courses"
  | "decks"
  | "interviews"
  | "prompts";

export const SECTIONS = [
  { key: "books", href: "/", label: "Books", icon: BookOpen },
  { key: "papers", href: "/papers", label: "Papers", icon: FileText },
  { key: "videos", href: "/videos", label: "Videos", icon: Video },
  { key: "courses", href: "/courses", label: "Courses", icon: GraduationCap },
  {
    key: "interviews",
    href: "/interviews",
    label: "Interview",
    icon: MessagesSquare,
  },
  { key: "decks", href: "/decks", label: "Cards", icon: Layers },
] satisfies readonly {
  key: SectionKey;
  href: string;
  label: string;
  icon: typeof BookOpen;
}[];

/**
 * Books, Papers, Videos, and Cards are peers, not features hidden inside the
 * reader. The switcher lives in the header so none of them looks like a mode of
 * another.
 *
 * Above `compact` only. Below it the header has no room for six destinations,
 * and the frame dissolves to a bottom tab bar instead — see `SectionTabBar`.
 */
export function SectionNav({ active }: { active: SectionKey }) {
  return (
    <nav
      aria-label="Library sections"
      className="hidden items-center gap-1 md:flex"
    >
      {SECTIONS.map(({ key, href, label, icon: Icon }) => (
        <Link
          key={key}
          href={href}
          aria-current={active === key ? "page" : undefined}
          // Icons until the row can afford words. Six labelled links measure
          // 606px; with the notifications, the theme and the account beside
          // them the group ran 862px, which overflowed an 812px landscape
          // phone outright and left the masthead 78px for the wordmark and the
          // status line at 1024. The labels come back at `xl`, where there is
          // room for both.
          aria-label={label}
          title={label}
          className={cn(
            "flex items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors xl:px-3",
            // Selection is the jade wash; hover is the neutral ground. They
            // were both `accent` before, so the current section and a hovered
            // one looked identical.
            active === key
              ? "bg-wash font-medium text-foreground"
              : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
          )}
        >
          <Icon aria-hidden className="size-4 shrink-0" />
          <span className="hidden xl:inline">{label}</span>
        </Link>
      ))}
    </nav>
  );
}

/**
 * The same six destinations as a bottom tab bar, below `compact`.
 *
 * The design system's compact rule is "frame dissolves: one task, bottom tab
 * bar, composer anchored". Before this existed the header switcher was simply
 * `hidden sm:block`, which left every section but the current one unreachable
 * on a phone — the library, the decks, and the interview setup were all behind
 * a control that was not rendered.
 *
 * In the layout column rather than fixed to the viewport, so it cannot cover
 * the composer that sits directly above it; `env(safe-area-inset-bottom)` keeps
 * it clear of the iOS home indicator.
 */
export function SectionTabBar({ active }: { active: SectionKey }) {
  return (
    <nav
      aria-label="Library sections"
      className="shrink-0 border-t border-border bg-background pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <ul className="flex items-stretch">
        {SECTIONS.map(({ key, href, label, icon: Icon }) => (
          <li key={key} className="min-w-0 flex-1">
            <Link
              href={href}
              aria-current={active === key ? "page" : undefined}
              className={cn(
                // 48px tall before the label wraps, over the 44px preferred
                // target: a tab bar is thumb-operated and gets the larger one.
                // No horizontal padding: six tabs on a 320px screen leave 53px
                // each, and "Interview" needs 51 of them. The label is the
                // destination's name — an ellipsis in it is worse than a tab
                // whose wash runs cell to cell.
                "flex min-h-12 flex-col items-center justify-center gap-1 px-0 py-2 transition-colors",
                // Never colour alone: the current tab carries the wash *and*
                // the heavier label, and `aria-current` names it outright.
                active === key
                  ? "bg-wash font-medium text-foreground"
                  : "text-muted-foreground",
              )}
            >
              <Icon aria-hidden className="size-4 shrink-0" />
              {/*
                `tracking-normal` because the eyebrow step carries 0.14em of
                letter-spacing for uppercase region labels, and these are
                sentence-case destinations: with the tracking, "Interview" ran
                66px wide in a 55px tab and truncated to "Intervi…".
              */}
              <span className="w-full truncate text-center text-eyebrow leading-tight tracking-normal">
                {label}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
