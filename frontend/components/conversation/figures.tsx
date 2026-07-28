"use client";

import { ImageOff, Images } from "lucide-react";
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import { formatPath } from "@/lib/citations";
import type { FigureRef } from "@/lib/types";
import { cn } from "@/lib/utils";

export function figureSource(figure: FigureRef): string {
  return `/api/books/${figure.book_id}/blocks/${figure.block_id}/image`;
}

/**
 * What a screen reader is told about a figure.
 *
 * Deliberately a location rather than a description: nothing in the pipeline
 * knows what the image depicts. Claiming otherwise would be worse than saying
 * where it came from. Real alt text needs captions, which is the documented
 * upgrade in study/figures.py.
 */
export function figureLabel(figure: FigureRef): string {
  const parts = formatPath(figure.path);
  return `Figure from ${parts.at(-1) ?? figure.path}, page ${figure.page}`;
}

function FigureImage({
  figure,
  className,
}: {
  figure: FigureRef;
  className?: string;
}) {
  const image = useAuthenticatedImage(figureSource(figure));

  if (image.status === "loading") {
    return <Skeleton className={cn("h-40 w-full", className)} />;
  }
  if (image.status === "failed") {
    return (
      <span className="flex items-center gap-2 px-3 py-6 text-xs text-muted-foreground">
        <ImageOff className="size-4 shrink-0" aria-hidden />
        Could not load the figure from page {figure.page}.
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={image.url}
      alt={figureLabel(figure)}
      className={cn("w-full bg-background object-contain", className)}
    />
  );
}

function Figure({ figure, onOpen }: { figure: FigureRef; onOpen: () => void }) {
  const parts = formatPath(figure.path);

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="group/figure block w-full overflow-hidden rounded-lg border border-border bg-card text-left transition-colors hover:border-citation"
      >
        <FigureImage figure={figure} className="max-h-56" />
        <span className="block border-t border-border px-2.5 py-1.5 text-xs text-muted-foreground">
          <span className="block truncate">{parts.at(-1) ?? figure.path}</span>
          <span className="block">p. {figure.page}</span>
        </span>
      </button>
    </li>
  );
}

export interface FiguresProps {
  figures: FigureRef[];
}

export function Figures({ figures }: FiguresProps) {
  const [opened, setOpened] = useState<FigureRef | null>(null);
  if (figures.length === 0) return null;

  return (
    <section aria-labelledby="figures-heading" className="space-y-2">
      <h4
        id="figures-heading"
        className="flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        <Images className="size-3.5" aria-hidden />
        {figures.length === 1
          ? "Figure on a cited page"
          : "Figures on cited pages"}
      </h4>

      <ul
        className={cn(
          "grid gap-2",
          figures.length === 1 ? "grid-cols-1" : "grid-cols-2",
        )}
      >
        {figures.map((figure) => (
          <Figure
            key={figure.block_id}
            figure={figure}
            onOpen={() => setOpened(figure)}
          />
        ))}
      </ul>

      <Dialog
        open={opened !== null}
        onOpenChange={(next) => !next && setOpened(null)}
      >
        <DialogContent className="max-w-4xl">
          {opened && (
            <>
              <DialogTitle className="text-sm font-medium">
                {formatPath(opened.path).at(-1) ?? opened.path}
              </DialogTitle>
              <DialogDescription className="text-xs">
                {formatPath(opened.path).join(" › ")} · page {opened.page}
              </DialogDescription>
              <FigureImage figure={opened} className="max-h-[70vh] rounded-md" />
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}
