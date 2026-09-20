"use client";

import { createContext, createElement, useContext, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  InlineFigure,
  figuresInReadingOrder,
} from "@/components/conversation/figures";
import {
  ambiguousCitationPages,
  figuresForMarker,
  formatPages,
  formatPath,
  markerPage,
  resolveMarker,
  splitOnCitations,
} from "@/lib/citations";
import { normalizeMath } from "@/lib/math";
import type { CitationRef, EvidenceRef, FigureRef } from "@/lib/types";
import { cn } from "@/lib/utils";

import "katex/dist/katex.min.css";

/** Minimal hast shapes; enough to rewrite text nodes without a tree library. */
interface HastText {
  type: "text";
  value: string;
}
interface HastElement {
  type: "element";
  tagName: string;
  properties: Record<string, string | number>;
  children: HastNode[];
}
type HastNode = HastText | HastElement | { type: string; children?: HastNode[] };

function hasChildren(node: HastNode): node is { type: string; children: HastNode[] } {
  return Array.isArray((node as { children?: unknown }).children);
}

/**
 * Replace citation markers in the rendered tree with `<citation-ref>` elements.
 *
 * Done as a rehype pass rather than a string replace on the raw answer so that
 * markers inside code spans and fenced blocks are left alone — a chip rendered
 * inside a code sample would be wrong, and the answer sometimes quotes them.
 */
function citationPlugin(evidence: EvidenceRef[], citations: CitationRef[]) {
  // KaTeX output is a tree of spans whose text is rendered maths; rewriting
  // inside it would corrupt the formula.
  const SKIP = new Set(["code", "pre", "math", "semantics", "annotation"]);

  return () => (tree: HastNode) => {
    const walk = (node: HastNode) => {
      if (!hasChildren(node)) return;
      if (node.type === "element") {
        const element = node as HastElement;
        if (SKIP.has(element.tagName)) return;
        // rehype-katex emits `<span class="katex">…</span>` subtrees.
        const className = element.properties?.className;
        const names = Array.isArray(className) ? className : [className];
        if (names.some((name) => String(name ?? "").startsWith("katex"))) {
          return;
        }
      }

      const rewritten: HastNode[] = [];
      let changed = false;

      for (const child of node.children) {
        if (child.type !== "text") {
          walk(child);
          rewritten.push(child);
          continue;
        }

        const segments = splitOnCitations(
          (child as HastText).value,
          evidence,
          citations,
        );
        if (segments.length === 1 && segments[0]?.type === "text") {
          rewritten.push(child);
          continue;
        }

        changed = true;
        for (const segment of segments) {
          if (segment.type === "text") {
            rewritten.push({ type: "text", value: segment.value });
          } else {
            rewritten.push({
              type: "element",
              tagName: "citation-ref",
              properties: {
                dataIndex: String(segment.index),
                dataMarker: segment.marker,
              },
              children: [],
            } satisfies HastElement);
          }
        }
      }

      if (changed) node.children = rewritten;
    };

    walk(tree);
  };
}

function CitationChip({
  index,
  reference,
  page,
  shownPage,
  onOpen,
}: {
  index: number;
  reference: EvidenceRef | undefined;
  /** The page this marker names, which can differ from the reference's first. */
  page?: number | null;
  /** Shown beside the number only when the number alone is ambiguous. */
  shownPage?: number | null;
  onOpen?: (reference: EvidenceRef, page?: number) => void;
}) {
  const chip = (
    <span
      data-citation=""
      role={reference && onOpen ? "button" : undefined}
      tabIndex={reference && onOpen ? 0 : undefined}
      onClick={
        reference && onOpen ? () => onOpen(reference, page ?? undefined) : undefined
      }
      onKeyDown={
        reference && onOpen
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpen(reference, page ?? undefined);
              }
            }
          : undefined
      }
      className={cn(
        // No horizontal margin: the marker sits directly against the word it
        // follows, and the sentence's own punctuation follows it. A margin
        // here reads as a stray space before the full stop. Consecutive
        // chips get their own separation instead.
        "inline-flex h-[1.15em] min-w-[1.15em] translate-y-[-0.15em] items-center justify-center rounded-[0.35em] bg-citation-muted px-[0.3em] align-middle font-sans text-[0.7em] font-semibold tabular-nums text-citation [&+[data-citation]]:ml-[0.2em]",
        reference &&
          onOpen &&
          "cursor-pointer transition-colors hover:bg-citation hover:text-citation-foreground",
        // The accessibility contract exempts these from the 24px target floor
        // — WCAG 2.5.8 exempts targets inline in a sentence — but still asks
        // that the hit area be padded vertically to 24px. A thumb aiming at a
        // 13px-tall marker set in running prose missed it more often than not.
        // A pseudo-element rather than padding, so growing the target does not
        // grow the line box it sits in.
        reference &&
          onOpen &&
          "relative after:absolute after:inset-x-0 after:top-1/2 after:h-6 after:-translate-y-1/2 after:content-['']",
      )}
    >
      {shownPage == null ? index : `${index}\u00b7${shownPage}`}
    </span>
  );

  if (!reference) return chip;

  const parts = formatPath(reference.path);
  return (
    <Tooltip>
      <TooltipTrigger asChild>{chip}</TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p className="font-medium">{reference.book_title ?? "This book"}</p>
        <p className="text-xs opacity-80">{parts.join(" › ")}</p>
        <p className="text-xs opacity-80">
          {page ? `p. ${page}` : formatPages(reference.pages)}
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

export interface AnswerProps {
  text: string;
  evidence: EvidenceRef[];
  citations: CitationRef[];
  figures?: FigureRef[];
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
  /** Connects this visible passage to the shared follow-along player. */
  narrationId?: string;
}

interface AnswerRenderState {
  evidence: EvidenceRef[];
  citations: CitationRef[];
  figures: FigureRef[];
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
  renderedFigures: Set<number>;
  /** Markers whose page is shown because their number no longer separates them. */
  ambiguousPages: Map<string, number>;
}

const AnswerRenderContext = createContext<AnswerRenderState | null>(null);

function useAnswerRenderState(): AnswerRenderState {
  const value = useContext(AnswerRenderContext);
  if (!value) throw new Error("Markdown answer rendered outside its context");
  return value;
}

/**
 * Static markdown component identities matter here. These renderers used to be
 * anonymous functions created inside `Answer` on every render. Scrolling,
 * dragging a side chat, or toggling the jump button then looked to React like
 * entirely new paragraph components, so inline figures unmounted, revoked
 * their blob URLs, and fetched again.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function MarkdownCitation({ node }: any) {
  const { evidence, citations, onOpenReference, ambiguousPages } =
    useAnswerRenderState();
  const index = Number(node?.properties?.dataIndex ?? 0);
  const marker = String(node?.properties?.dataMarker ?? "");
  const resolved = resolveMarker(marker, evidence, citations);
  return (
    <CitationChip
      index={index}
      reference={evidence[index - 1]}
      page={markerPage(resolved)}
      shownPage={ambiguousPages.get(marker) ?? null}
      onOpen={onOpenReference}
    />
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function MarkdownParagraph({ children, node }: any) {
  const { evidence, citations, figures, renderedFigures } =
    useAnswerRenderState();
  const markers: string[] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const walk = (element: any) => {
    if (!element) return;
    if (element.tagName === "citation-ref") {
      markers.push(String(element.properties?.dataMarker ?? ""));
    }
    (element.children ?? []).forEach(walk);
  };
  (node?.children ?? []).forEach(walk);

  const attached: FigureRef[] = [];
  for (const marker of markers) {
    const resolved = resolveMarker(marker, evidence, citations);
    for (const figure of figuresForMarker(figures, resolved)) {
      if (renderedFigures.has(figure.block_id)) continue;
      renderedFigures.add(figure.block_id);
      attached.push(figure);
    }
  }

  return (
    <>
      <p data-narration-block={narrationBlockKey(node)}>{children}</p>
      {attached.map((figure) => (
        <InlineFigure key={figure.block_id} figure={figure} />
      ))}
    </>
  );
}

// `react-markdown` preserves source positions. Sharing that stable line key
// with the narration parser makes follow-along deterministic even when a
// heading gains spoken punctuation or the same sentence appears twice.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function narrationBlockKey(node: any): string | undefined {
  const line = Number(node?.position?.start?.line);
  return Number.isFinite(line) && line > 0 ? `line-${line}` : undefined;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function MarkdownNarrationBlock({ children, node, ...props }: any) {
  const tag = String(node?.tagName ?? "div");
  return createElement(
    tag,
    { ...props, "data-narration-block": narrationBlockKey(node) },
    children,
  );
}

const ANSWER_MARKDOWN_COMPONENTS = {
  "citation-ref": MarkdownCitation,
  p: MarkdownParagraph,
  h1: MarkdownNarrationBlock,
  h2: MarkdownNarrationBlock,
  h3: MarkdownNarrationBlock,
  h4: MarkdownNarrationBlock,
  h5: MarkdownNarrationBlock,
  h6: MarkdownNarrationBlock,
  blockquote: MarkdownNarrationBlock,
  ul: MarkdownNarrationBlock,
  ol: MarkdownNarrationBlock,
  pre: MarkdownNarrationBlock,
  table: MarkdownNarrationBlock,
} as never;

export function Answer({
  text,
  evidence,
  citations,
  figures = [],
  onOpenReference,
  narrationId,
}: AnswerProps) {
  const plugin = useMemo(
    () => citationPlugin(evidence, citations),
    [evidence, citations],
  );
  const source = useMemo(() => normalizeMath(text), [text]);
  const orderedFigures = useMemo(
    () => figuresInReadingOrder(figures),
    [figures],
  );
  // Each markdown pass needs a fresh set so the first mention wins again. The
  // renderer *types* above stay static, which is what preserves their DOM and
  // hook state while this context value updates.
  const ambiguousPages = useMemo(
    () => ambiguousCitationPages(citations),
    [citations],
  );
  const renderState: AnswerRenderState = {
    evidence,
    citations,
    figures: orderedFigures,
    onOpenReference,
    renderedFigures: new Set<number>(),
    ambiguousPages,
  };

  return (
    // Marks the answer body as the region a side chat can be anchored to. The
    // reference cards and controls around it are interface, not passages: a
    // quote of "8 Advanced Practice" anchors nothing worth asking about.
    <AnswerRenderContext.Provider value={renderState}>
      <div
        className="answer-prose"
        data-answer=""
        data-narration-anchor={narrationId}
      >
        <ReactMarkdown
          remarkPlugins={[remarkMath]}
          // KaTeX runs before the citation pass so that markers are never
          // rewritten inside a rendered formula.
          rehypePlugins={[[rehypeKatex, { throwOnError: false }], plugin]}
          components={ANSWER_MARKDOWN_COMPONENTS}
        >
          {source}
        </ReactMarkdown>
      </div>
    </AnswerRenderContext.Provider>
  );
}
