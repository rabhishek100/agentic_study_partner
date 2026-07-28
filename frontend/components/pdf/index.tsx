"use client";

import dynamic from "next/dynamic";

import { Skeleton } from "@/components/ui/skeleton";

export type { PdfTarget } from "./target";

/**
 * pdf.js reaches for browser globals (`DOMMatrix`) while its module is being
 * evaluated, so importing it anywhere reachable from the server render breaks
 * the production prerender. Loading it only in the browser also keeps a
 * sizeable dependency out of the initial bundle for readers who never open a
 * document.
 */
export const PdfViewer = dynamic(
  () => import("./pdf-viewer").then((module) => module.PdfViewer),
  {
    ssr: false,
    loading: () => (
      <div className="h-full bg-card p-4">
        <Skeleton className="h-full w-full" />
      </div>
    ),
  },
);
