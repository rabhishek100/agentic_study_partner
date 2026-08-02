"use client";

import { PanelLeft } from "lucide-react";
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
import { cn } from "@/lib/utils";

export interface AppShellProps {
  /** Library, upload, and settings. Shown as a rail, or a sheet on mobile. */
  rail: React.ReactNode;
  /** Short status line for the current conversation. */
  status: React.ReactNode;
  /** Account controls, right-aligned in the header. */
  account: React.ReactNode;
  children: React.ReactNode;
  /** The document pane, docked right of the conversation when open. */
  aside?: React.ReactNode;
}

export function AppShell({
  rail,
  status,
  account,
  children,
  aside,
}: AppShellProps) {
  const [railOpen, setRailOpen] = useState(false);

  return (
    <div className="flex h-dvh flex-col">
      <a
        href="#question"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:text-sm focus:shadow-md"
      >
        Skip to the question box
      </a>

      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-3 sm:px-4">
        <Sheet open={railOpen} onOpenChange={setRailOpen}>
          <SheetTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              className={cn(!aside && "lg:hidden")}
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

        <div className="flex min-w-0 items-center gap-2.5">
          <BrandMark size="sm" />
          <div className="min-w-0">
            <p className="truncate font-heading text-sm font-medium leading-tight">
              Agentic Study Partner
            </p>
            <div className="truncate text-xs text-muted-foreground">
              {status}
            </div>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <ThemeToggle />
          {account}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {!aside && (
          <aside className="hidden w-72 shrink-0 border-r border-border bg-sidebar lg:block xl:w-80">
            {rail}
          </aside>
        )}
        <SplitPane aside={aside ?? null}>
          <main className="flex min-w-0 flex-1 flex-col">{children}</main>
        </SplitPane>
      </div>
    </div>
  );
}
