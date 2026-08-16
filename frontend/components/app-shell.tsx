"use client";

import { PanelLeft } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { BrandMark } from "@/components/brand-mark";
import { HEADER_INSET } from "@/lib/floating-window";
import { SplitPane } from "@/components/pdf/split-pane";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

export interface AppShellProps {
  /** Library, upload, and settings. Shown as a rail, or a sheet on mobile. */
  rail: React.ReactNode;
  /** Some workspaces need the mobile drawer without reserving a desktop rail. */
  railMode?: "responsive" | "drawer-only";
  /** Switches between the library's sections; books and videos are peers. */
  nav?: React.ReactNode;
  /** Short status line for the current conversation. */
  status: React.ReactNode;
  /** Account controls, right-aligned in the header. */
  account: React.ReactNode;
  /** Restores a document that has been minimized. */
  documentControl?: React.ReactNode;
  /** Lists the conversation's side chats, including closed ones. */
  sideChatControl?: React.ReactNode;
  children: React.ReactNode;
  /**
   * The right region's content, docked beside the conversation. Pass it for as
   * long as it exists — closing it is `asideHidden`, not `null`. Unmounting is
   * what loses a document's scroll position and re-fetches its pages.
   */
  aside?: React.ReactNode;
  /** Closed but not discarded. */
  asideHidden?: boolean;
  /** Names the region for its current mode. */
  asideLabel?: string;
  /** Floating side-chat windows, positioned against the viewport. */
  overlay?: React.ReactNode;
}


const RAIL_STORAGE_KEY = "asp:rail-collapsed";

/**
 * Whether the docked rail is collapsed, remembered across visits.
 *
 * Read after mount rather than during render: the server has no localStorage,
 * and seeding state from it directly would hydrate against a different value.
 */
function useRailCollapsed(): [boolean, (next: boolean) => void] {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem(RAIL_STORAGE_KEY) === "true");
  }, []);

  const update = useCallback((next: boolean) => {
    setCollapsed(next);
    window.localStorage.setItem(RAIL_STORAGE_KEY, String(next));
  }, []);

  return [collapsed, update];
}

export function AppShell({
  rail,
  railMode = "responsive",
  nav,
  status,
  account,
  documentControl,
  sideChatControl,
  children,
  aside,
  asideHidden = false,
  asideLabel,
  overlay,
}: AppShellProps) {
  const [railOpen, setRailOpen] = useState(false);
  const [railCollapsed, setRailCollapsed] = useRailCollapsed();

  return (
    <div
      data-slot="app-shell"
      className="flex h-dvh max-h-dvh w-full flex-col overflow-hidden"
    >
      <a
        href="#question"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:text-sm focus:shadow-md"
      >
        Skip to the question box
      </a>

      {/*
        The height comes from HEADER_INSET rather than an `h-14` class, because
        the floating side-chat windows are clamped, snapped, and cascaded
        against that constant. They were two independent numbers that happened
        to agree, with nothing asserting they still did — the floating-window
        tests assert against the imported symbol, so they pass at any value.

        Setting it in pixels also fixes a real drift: `h-14` is rem-based, while
        HEADER_INSET is compared against raw clientY/innerHeight. Under a raised
        browser font size the header grew past 56 and windows sat underneath it.
        Browser *zoom* scales both together, so only font size was affected.
      */}
      <header
        style={{ height: HEADER_INSET }}
        className="flex shrink-0 items-center gap-3 border-b border-border bg-background px-3 sm:px-4"
      >
        {rail && railMode === "responsive" ? (
          <Button
            variant="ghost"
            size="icon-sm"
            className="hidden lg:inline-flex"
            aria-label={
              railCollapsed ? "Show the library panel" : "Hide the library panel"
            }
            aria-pressed={!railCollapsed}
            onClick={() => setRailCollapsed(!railCollapsed)}
          >
            <PanelLeft aria-hidden />
          </Button>
        ) : null}

        {rail ? (
          <Sheet open={railOpen} onOpenChange={setRailOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className={cn(
                  railMode === "responsive" && "lg:hidden",
                  railMode === "drawer-only" && "sm:hidden",
                )}
                aria-label="Open the library panel"
              >
                <PanelLeft aria-hidden />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-80 bg-sidebar p-0">
              <SheetTitle className="sr-only">Library and settings</SheetTitle>
              <SheetDescription className="sr-only">
                Choose a book, change the search method, and upload new books.
              </SheetDescription>
              {rail}
            </SheetContent>
          </Sheet>
        ) : null}

        <div className="flex min-w-0 items-center gap-3">
          <BrandMark size="sm" />
          <div className="min-w-0">
            <p className="truncate font-serif text-base font-medium leading-tight tracking-tight">
              Mugensei
            </p>
            <div className="truncate text-xs text-muted-foreground">
              {status}
            </div>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {nav ? <div className="hidden sm:block">{nav}</div> : null}
          {sideChatControl}
          {documentControl}
          <ThemeToggle />
          {account}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/*
          The rail no longer disappears when a document opens. It used to be
          gated on `!aside`, so the frame reorganised itself underneath the
          reader the moment they followed a citation — the one thing a fixed
          frame is supposed to never do. Reclaiming that space is now the
          reader's decision, through the masthead control, and it is remembered.
        */}
        {rail && railMode === "responsive" ? (
          <aside
            className={cn(
              "hidden w-72 shrink-0 border-r border-border bg-sidebar xl:w-80",
              railCollapsed ? "lg:hidden" : "lg:block",
            )}
          >
            {rail}
          </aside>
        ) : null}
        <SplitPane
          aside={aside ?? null}
          asideHidden={asideHidden}
          asideLabel={asideLabel}
        >
          <main className="flex min-w-0 flex-1 flex-col">{children}</main>
        </SplitPane>
      </div>

      {/*
        Outside the scrolling layout on purpose: side chat windows are
        positioned against the viewport, so nesting them inside a pane would
        clip them at that pane's edge.
      */}
      {overlay}
    </div>
  );
}
