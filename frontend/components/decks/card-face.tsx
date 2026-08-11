"use client";

import { Lightbulb, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import {
  CARD_TYPE_LABELS,
  type DeckCard,
  type DeckFigure,
  type McqOption,
  type QueueCard,
} from "@/lib/deck-types";
import { cn } from "@/lib/utils";

/**
 * Citation markers are stripped from card prose before it is shown.
 *
 * On an answer in the chat they are the point: you click one to see where a
 * claim came from. On a card they are noise — the card is read in three
 * seconds and its sources are listed underneath it anyway, resolved and
 * clickable. The grounding is enforced at generation time either way.
 */
const MARKER = /\s*\[(?:N\d+:P\d+|S\d+)\]/g;

export function plain(text: string): string {
  return text.replace(MARKER, "").replace(/\s{2,}/g, " ").trim();
}

/** Preserve source-authored line breaks and tables on long exercise fronts. */
export function sourceQuestion(text: string): string {
  return text.replace(MARKER, "").replace(/\n{3,}/g, "\n\n").trim();
}

function timestamp(ms: number | null): string {
  if (ms === null) return "";
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${minutes}:${pad(seconds)}`;
}

function figureSource(figure: DeckFigure, videoId: string | null): string | null {
  if (figure.kind === "book_image" && figure.book_id && figure.block_id) {
    return `/api/books/${figure.book_id}/blocks/${figure.block_id}/image`;
  }
  if (figure.kind === "lecture_frame" && videoId && figure.frame_id) {
    return `/api/videos/${videoId}/frames/${figure.frame_id}/image`;
  }
  return null;
}

function CardFigure({
  figure,
  videoId,
}: {
  figure: DeckFigure;
  videoId: string | null;
}) {
  const source = figureSource(figure, videoId);
  const image = useAuthenticatedImage(source ?? "");
  if (!source) return null;

  return (
    <figure className="overflow-hidden rounded-lg border border-border bg-card">
      {image.status === "loading" ? (
        <Skeleton className="h-40 w-full" />
      ) : image.status === "ready" ? (
        <img
          src={image.url}
          alt={figure.caption ?? "Figure from the source material"}
          className="max-h-64 w-full bg-white object-contain"
        />
      ) : null}
      {figure.caption ? (
        <figcaption className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
          {figure.caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </h4>
      <ul className="space-y-1 text-sm">
        {items.map((item, index) => (
          <li key={index} className="flex gap-2">
            <span aria-hidden className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground/60" />
            <span>{plain(item)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The one line you would actually say out loud. Shown largest, on purpose. */
function SayItAloud({ text }: { text: string }) {
  if (!text.trim()) return null;
  return (
    <p className="rounded-lg border-l-2 border-primary bg-accent/40 px-3 py-2 font-heading text-base leading-snug">
      {plain(text)}
    </p>
  );
}

export function CardFront({
  card,
  selected,
  onSelect,
}: {
  card: DeckCard;
  /** MCQ only: which option the reader picked before revealing. */
  selected?: McqOption["label"] | null;
  onSelect?: (label: McqOption["label"]) => void;
}) {
  return (
    <div className="space-y-4">
      <p className="whitespace-pre-wrap font-heading text-xl leading-snug sm:text-2xl">
        {sourceQuestion(card.front)}
      </p>
      {card.card_type === "mcq" ? (
        <ul className="space-y-2">
          {card.back.options.map((option) => (
            <li key={option.label}>
              <button
                type="button"
                onClick={() => onSelect?.(option.label)}
                aria-pressed={selected === option.label}
                className={cn(
                  "flex w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  selected === option.label
                    ? "border-primary bg-accent"
                    : "border-border hover:bg-accent/50",
                )}
              >
                <span className="font-mono text-xs text-muted-foreground">
                  {option.label}
                </span>
                <span>{plain(option.text)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function CardBackFace({
  item,
  selected,
}: {
  item: QueueCard;
  selected?: McqOption["label"] | null;
}) {
  const { card } = item;
  const back = card.back;

  return (
    <div className="space-y-4">
      {card.card_type === "mcq" ? (
        <ul className="space-y-2">
          {back.options.map((option) => (
            <li
              key={option.label}
              className={cn(
                "rounded-lg border px-3 py-2.5 text-sm",
                option.correct
                  ? "border-emerald-600/60 bg-emerald-500/10"
                  : selected === option.label
                    ? "border-destructive/60 bg-destructive/10"
                    : "border-border opacity-70",
              )}
            >
              <div className="flex items-start gap-3">
                <span className="font-mono text-xs text-muted-foreground">
                  {option.label}
                </span>
                <div className="space-y-1">
                  <p>{plain(option.text)}</p>
                  {option.rationale ? (
                    <p className="text-xs text-muted-foreground">
                      {plain(option.rationale)}
                    </p>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <SayItAloud text={back.say_it_aloud} />
      )}

      {back.answer.trim() ? (
        <p className="whitespace-pre-wrap text-sm leading-relaxed">
          {plain(back.answer)}
        </p>
      ) : null}

      {back.why_it_matters.trim() ? (
        <p className="text-sm leading-relaxed text-muted-foreground">
          {plain(back.why_it_matters)}
        </p>
      ) : null}

      {/*
        A design card is read at a glance, so its structure is laid out as
        columns rather than run together as prose.
      */}
      {card.card_type === "system_design" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Bullets title="Components" items={back.components} />
          <Bullets title="Data flow" items={back.data_flow} />
          <Bullets title="Trade-offs" items={back.trade_offs} />
          <Bullets title="Failure modes" items={back.failure_modes} />
        </div>
      ) : (
        <Bullets title="Key points" items={back.key_points} />
      )}

      {card.figures.length > 0 ? (
        <div className="space-y-3">
          {card.figures.map((figure, index) => (
            <CardFigure key={index} figure={figure} videoId={item.video_id} />
          ))}
        </div>
      ) : null}

      {card.interview_angle ? (
        <div className="rounded-lg border border-dashed border-border bg-muted/40 p-3">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Sparkles aria-hidden className="size-3.5" />
            Interview angle
            {/*
              Said plainly, every time. This part is not from the book, and a
              reader who cannot tell which half is grounded learns the wrong
              thing with full confidence.
            */}
            <Badge variant="outline" className="ml-1 font-normal">
              model knowledge, not from the source
            </Badge>
          </p>
          <p className="text-sm leading-relaxed">{card.interview_angle}</p>
        </div>
      ) : null}
    </div>
  );
}

export function CardSources({
  item,
  onOpenSource,
}: {
  item: QueueCard;
  onOpenSource?: (citation: QueueCard["card"]["citations"][number]) => void;
}) {
  const { card } = item;
  if (card.citations.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted-foreground">Source:</span>
      {card.citations.map((citation) => {
        const label =
          citation.page !== null
            ? `p. ${citation.page}`
            : timestamp(citation.start_ms) || `[${citation.evidence_rank}]`;
        return (
          <button
            key={citation.marker}
            type="button"
            onClick={() => onOpenSource?.(citation)}
            className="rounded-md border border-border px-2 py-0.5 font-mono text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function CardMeta({ card }: { card: DeckCard }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {card.answer_source === "printed_in_book" ? (
        <Badge variant="default" className="bg-emerald-600/90 text-white font-normal hover:bg-emerald-600">
          Original Book Answer
        </Badge>
      ) : card.answer_source === "rag_generated" ? (
        <Badge variant="secondary" className="font-normal border border-primary/20">
          Grounded RAG Answer
        </Badge>
      ) : null}
      <Badge variant="secondary" className="font-normal">
        {CARD_TYPE_LABELS[card.card_type]}
      </Badge>
      <Badge variant="outline" className="font-normal capitalize">
        {card.difficulty}
      </Badge>
      {card.interview_priority >= 4 ? (
        <Badge className="gap-1 font-normal" title={card.priority_reason}>
          <Lightbulb aria-hidden className="size-3" />
          High yield
        </Badge>
      ) : null}
    </div>
  );
}
