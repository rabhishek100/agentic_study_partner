"use client";

import { AnalyticsObserver } from "@/components/analytics-observer";

import { ThemeProvider } from "next-themes";

import { TooltipProvider } from "@/components/ui/tooltip";
import { NarrationPlayerBar } from "@/components/conversation/narration-player-bar";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      <TooltipProvider delayDuration={200}>
        <AnalyticsObserver />
        {children}
        <NarrationPlayerBar />
      </TooltipProvider>
    </ThemeProvider>
  );
}
