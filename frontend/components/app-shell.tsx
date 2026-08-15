"use client";

import { Menu } from "lucide-react";
import { useState } from "react";

import { BrandMark } from "@/components/brand-mark";
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

export interface AppShellProps {
  /** Library, upload, and settings. Shown as a rail, or a sheet on mobile. */
  rail: React.ReactNode;
  /** Some workspaces need the mobile drawer without reserving a desktop rail. */
  railMode?: "responsive" | "drawer-only";
  /** Switches between the library's sections; books and videos are peers. */
  nav?: React.ReactNode;
  /** Short status line for the current conversation. */
  status?: React.ReactNode;
  /** Account controls, right-aligned in the header. */
  account: React.ReactNode;
  /** Restores a document that has been minimized. */
  documentControl?: React.ReactNode;
  /** Lists the conversation's side chats, including closed ones. */
  sideChatControl?: React.ReactNode;
  children: React.ReactNode;
  /** The document pane, docked right of the conversation when open. */
  aside?: React.ReactNode;
  /** Floating side-chat windows, positioned against the viewport. */
  overlay?: React.ReactNode;
  /** Workspace-specific context, rendered below the global navigation. */
  contextBar?:
    | React.ReactNode
    | ((controls: { openRail: () => void }) => React.ReactNode);
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
  overlay,
  contextBar,
}: AppShellProps) {
  const [railOpen, setRailOpen] = useState(false);

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

      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-3 sm:px-4">
        {rail || nav ? (
          <Sheet open={railOpen} onOpenChange={setRailOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Open menu"
              >
                <Menu aria-hidden />
              </Button>
            </SheetTrigger>
            <SheetContent
              side="left"
              className="flex w-80 flex-col bg-sidebar p-0"
            >
              <SheetTitle className="sr-only">Menu</SheetTitle>
              <SheetDescription className="sr-only">
                Navigate between workspaces and access tools for this screen.
              </SheetDescription>
              {nav ? (
                <div className="shrink-0 border-b border-border p-3 [&>nav]:grid [&>nav]:grid-cols-1 [&>nav>a]:justify-start">
                  {nav}
                </div>
              ) : null}
              {rail ? <div className="min-h-0 flex-1">{rail}</div> : null}
            </SheetContent>
          </Sheet>
        ) : null}

        <div className="flex min-w-0 items-center gap-2.5">
          <BrandMark size="sm" />
          <div className="min-w-0">
            <p className="truncate font-heading text-base font-medium leading-tight">
              Mugensei
            </p>
            {status ? (
              <div className="truncate text-xs text-muted-foreground">
                {status}
              </div>
            ) : null}
          </div>
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          {nav ? <div className="hidden sm:block">{nav}</div> : null}
          {sideChatControl}
          {documentControl}
          <ThemeToggle />
          {account}
        </div>
      </header>

      {contextBar ? (
        <div className="shrink-0">
          {typeof contextBar === "function"
            ? contextBar({ openRail: () => setRailOpen(true) })
            : contextBar}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {!aside && rail && railMode === "responsive" ? (
          <aside className="hidden w-72 shrink-0 border-r border-border bg-sidebar lg:block xl:w-80">
            {rail}
          </aside>
        ) : null}
        <SplitPane aside={aside ?? null}>
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
