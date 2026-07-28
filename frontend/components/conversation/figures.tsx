"use client";

import { ImageOff, Images } from "lucide-react";
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { CaptionText } from "@/components/conversation/caption-text";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import { formatPath } from "@/lib/citations";
import type { FigureRef } from "@/lib/types";
import { cn } from "@/lib/utils";

export function figureSource(figure: FigureRef): string {
  return `/api/books/${figure.book_id}/blocks/${figure.block_id}/image`;
}

/** What a screen reader is told about a figure. */
export function figureLabel(figure: FigureRef): string {
  // A caption written by the vision model at ingest is real alt text: it says
  // what the figure shows. Without one, the honest fallback is where it came
  // from, because nothing else in the pipeline knows.
  if (figure.caption) return figure.caption;
  const parts = formatPath(figure.path);
  return `Figure from ${parts.at(-1) ?? figure.path}, page ${figure.page}`;
}

/**
 * A figure set beside the passage that cites it.
 *
 * This is the main path now that captions make figures retrievable: the model
 * cites the figure where it discusses what it shows, and the figure lands
 * there rather than in a gallery the reader has to reconcile with the prose.
 */
export function InlineFigure({ figure }: { figure: FigureRef }) {
  const [opened, setOpened] = useState(false);
  const parts = formatPath(figure.path);

  return (
    <figure className="my-4 overflow-hidden rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpened(true)}
        aria-label={`Enlarge: ${figureLabel(figure)}`}
        className="block w-full"
      >
        <FigureImage figure={figure} className="max-h-80" />
      </button>
      <figcaption className="border-t border-border px-3 py-2 font-sans text-xs leading-relaxed text-muted-foreground">
        {figure.caption && (
          <span className="block text-foreground">
            <CaptionText>{figure.caption}</CaptionText>
          </span>
        )}
        <span className="block">
          {parts.at(-1) ?? figure.path} · p. {figure.page}
        </span>
      </figcaption>

      <FigureLightbox
        figure={opened ? figure : null}
        onClose={() => setOpened(false)}
      />
    </figure>
  );
}

function FigureLightbox({
  figure,
  onClose,
}: {
  figure: FigureRef | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={figure !== null} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-4xl">
        {figure && (
          <>
            <DialogTitle className="text-sm font-medium">
              {formatPath(figure.path).at(-1) ?? figure.path}
            </DialogTitle>
            <DialogDescription className="text-xs" asChild>
              <div>
                {figure.caption ? (
                  <CaptionText>{figure.caption}</CaptionText>
                ) : (
                  `${formatPath(figure.path).join(" › ")} · page ${figure.page}`
                )}
              </div>
            </DialogDescription>
            <FigureImage figure={figure} className="max-h-[70vh] rounded-md" />
          </>
        )}
      </DialogContent>
    </Dialog>
  );
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
          ? "Also on a cited page"
          : "Also on cited pages"}
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

      <FigureLightbox figure={opened} onClose={() => setOpened(null)} />
    </section>
  );
}
