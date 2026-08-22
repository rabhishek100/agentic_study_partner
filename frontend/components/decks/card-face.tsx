"use client";

import { ChevronDown, Lightbulb, Sparkles, Timer } from "lucide-react";

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

export function isSourceAuthoredCard(card: DeckCard): boolean {
  return Boolean(card.source_item_key && card.source_item_kind);
}

export function sourceItemKindLabel(card: DeckCard): string | null {
  if (card.source_item_kind === "worked_example") return "Worked example";
  if (card.source_item_kind === "exercise") return "Exercise";
  return null;
}

export function sourceItemPlacementLabel(card: DeckCard): string | null {
  if (card.source_item_placement === "end_of_chapter") {
    return "End of chapter";
  }
  if (card.source_item_placement === "inline") return "Inline";
  return null;
}

export function solutionLabel(card: DeckCard): string {
  if (isSourceAuthoredCard(card)) {
    return card.answer_source === "printed_in_book"
      ? "Book solution"
      : "Grounded solution";
  }
  return card.answer_source === "printed_in_book"
    ? "Answer from book"
    : "Grounded answer";
}

export function SourceItemMeta({ card }: { card: DeckCard }) {
  if (!isSourceAuthoredCard(card)) return null;
  const kind = sourceItemKindLabel(card);
  const placement = sourceItemPlacementLabel(card);

  return (
    <>
      {card.source_label ? <span>{card.source_label}</span> : null}
      {card.source_label && kind ? <span aria-hidden>·</span> : null}
      {kind ? <span>{kind}</span> : null}
      {kind && placement ? <span aria-hidden>·</span> : null}
      {placement ? <span>{placement}</span> : null}
    </>
  );
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
      <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </h4>
      <ul className="space-y-1 text-sm">
        {items.map((item, index) => (
          <li key={index} className="flex gap-2">
            <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-divider" />
            <span>{plain(item)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The one line you would actually say out loud. Shown first, on purpose. */
function SayItAloud({
  text,
  studyMode = false,
}: {
  text: string;
  studyMode?: boolean;
}) {
  if (!text.trim()) return null;
  if (studyMode) {
    return (
      <section aria-labelledby="short-answer-heading">
        <h3
          id="short-answer-heading"
          className="mb-2 flex items-center gap-2 font-serif text-base font-medium text-primary"
        >
          <Timer aria-hidden className="size-4" />
          30-second answer
        </h3>
        <p className="border-l-2 border-primary py-1 pl-4 font-serif text-base leading-relaxed sm:text-base">
          {plain(text)}
        </p>
      </section>
    );
  }
  return (
    <p className="rounded-lg border-l-2 border-primary bg-surface-hover px-3 py-2 font-serif text-base leading-snug">
      {plain(text)}
    </p>
  );
}

export function CardFront({
  card,
  selected,
  onSelect,
  studyMode = false,
}: {
  card: DeckCard;
  /** MCQ only: which option the reader picked before revealing. */
  selected?: McqOption["label"] | null;
  onSelect?: (label: McqOption["label"]) => void;
  studyMode?: boolean;
}) {
  return (
    <div className="space-y-4">
      <p
        className={cn(
          "whitespace-pre-wrap font-serif",
          studyMode
            ? "text-lg leading-[1.55] sm:text-xl"
            : "text-xl leading-snug sm:text-2xl",
        )}
      >
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
                  "flex w-full items-start gap-3 rounded-lg border px-3 py-3 text-left text-sm transition-colors",
                  "",
                  selected === option.label
                    ? "border-primary bg-accent"
                    : "border-border hover:bg-surface-hover",
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

interface AnswerSection {
  label: string;
  title: string;
  body: string;
}

/**
 * Source-question answers repeat their printed labels. Presenting those
 * existing boundaries as disclosure rows makes a long worked solution
 * scannable without inventing, rewriting, or dropping any answer content.
 */
export function structuredAnswer(
  answer: string,
  keyPoints: string[] = [],
): AnswerSection[] {
  const text = plain(answer);
  const matches = Array.from(text.matchAll(/(?:^|\s)(\([a-z]\))\s+/gi));
  if (matches.length < 2) return [];

  return matches.map((match, index) => {
    const start = (match.index ?? 0) + match[0].length;
    const end = matches[index + 1]?.index ?? text.length;
    const body = text.slice(start, end).trim();
    const firstSentence = body.split(/(?<=[.!?])\s+/)[0] ?? body;
    return {
      label: (match[1] ?? "").toLowerCase(),
      title: plain(keyPoints[index] ?? firstSentence),
      body,
    };
  });
}

export function CardBackFace({
  item,
  selected,
  studyMode = false,
  hideSummary = false,
}: {
  item: QueueCard;
  selected?: McqOption["label"] | null;
  studyMode?: boolean;
  /** The caller already rendered the short answer above this detail view. */
  hideSummary?: boolean;
}) {
  const { card } = item;
  const back = card.back;
  const answerSections = studyMode
    ? structuredAnswer(back.answer, back.key_points)
    : [];

  return (
    <div className="space-y-4">
      {card.card_type === "mcq" ? (
        <ul className="space-y-2">
          {back.options.map((option) => (
            <li
              key={option.label}
              className={cn(
                "rounded-lg border px-3 py-3 text-sm",
                option.correct
                  ? "border-positive bg-wash"
                  : selected === option.label
                    ? "border-destructive bg-destructive-wash"
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
      ) : hideSummary ? null : (
        <SayItAloud text={back.say_it_aloud} studyMode={studyMode} />
      )}

      {back.answer.trim() ? (
        answerSections.length > 1 ? (
          <section aria-label="Detailed answer">
            <div className="divide-y divide-border border-y border-border">
              {answerSections.map((section) => (
                <details key={section.label} className="group">
                  <summary className="flex cursor-pointer list-none items-center gap-3 py-3 text-sm [&::-webkit-details-marker]:hidden">
                    <span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
                      {section.label.slice(1, -1)}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-serif font-medium">
                      {section.title}
                    </span>
                    <ChevronDown
                      aria-hidden
                      className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180"
                    />
                  </summary>
                  <p className="whitespace-pre-wrap pb-4 pl-8 text-sm leading-7">
                    {section.body}
                  </p>
                </details>
              ))}
            </div>
          </section>
        ) : (
          <div>
            {studyMode ? (
              <h3 className="mb-2 text-sm font-medium text-muted-foreground">
                Detailed answer
              </h3>
            ) : null}
            <p className="whitespace-pre-wrap text-sm leading-7">
              {plain(back.answer)}
            </p>
          </div>
        )
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
      ) : studyMode && answerSections.length > 1 ? null : (
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
        <div className="rounded-lg border border-dashed border-border bg-surface p-3">
          <p className="mb-1 flex items-center gap-2 text-xs font-medium text-muted-foreground">
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
  const separated = isSourceAuthoredCard(card);
  const questionCitations = card.question_citations ?? [];
  const answerCitations = card.answer_citations ?? [];
  if (
    card.citations.length === 0 &&
    questionCitations.length === 0 &&
    answerCitations.length === 0
  ) {
    return null;
  }

  function CitationGroup({
    label,
    citations,
  }: {
    label: string;
    citations: QueueCard["card"]["citations"];
  }) {
    if (citations.length === 0) return null;
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">{label}:</span>
        {citations.map((citation) => {
          const location =
            citation.page !== null
              ? `p. ${citation.page}`
              : timestamp(citation.start_ms) || `[${citation.evidence_rank}]`;
          return (
            <button
              key={citation.marker}
              type="button"
              aria-label={`${label}: ${location}`}
              onClick={() => onOpenSource?.(citation)}
              className="rounded-md border border-border px-2 py-1 font-mono text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {location}
            </button>
          );
        })}
      </div>
    );
  }

  if (separated && (questionCitations.length || answerCitations.length)) {
    return (
      <div className="space-y-2">
        <CitationGroup label="Question source" citations={questionCitations} />
        <CitationGroup label="Solution source" citations={answerCitations} />
      </div>
    );
  }

  return <CitationGroup label="Source" citations={card.citations} />;
}

export function CardMeta({ card }: { card: DeckCard }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {card.answer_source === "printed_in_book" ? (
        <Badge variant="default" className="bg-wash text-white font-normal hover:bg-positive">
          {isSourceAuthoredCard(card) ? "Book solution" : "Original Book Answer"}
        </Badge>
      ) : card.answer_source === "rag_generated" ? (
        <Badge variant="secondary" className="font-normal border border-action">
          {isSourceAuthoredCard(card)
            ? "Grounded solution"
            : "Grounded RAG Answer"}
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
