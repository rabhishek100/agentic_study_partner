"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { InlineFigure } from "@/components/conversation/figures";
import { formatPages, formatPath, splitOnCitations } from "@/lib/citations";
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
  onOpen,
}: {
  index: number;
  reference: EvidenceRef | undefined;
  onOpen?: (reference: EvidenceRef) => void;
}) {
  const chip = (
    <span
      data-citation=""
      role={reference && onOpen ? "button" : undefined}
      tabIndex={reference && onOpen ? 0 : undefined}
      onClick={reference && onOpen ? () => onOpen(reference) : undefined}
      onKeyDown={
        reference && onOpen
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpen(reference);
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
      )}
    >
      {index}
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
        <p className="text-xs opacity-80">{formatPages(reference.pages)}</p>
      </TooltipContent>
    </Tooltip>
  );
}

export interface AnswerProps {
  text: string;
  evidence: EvidenceRef[];
  citations: CitationRef[];
  figures?: FigureRef[];
  onOpenReference?: (reference: EvidenceRef) => void;
}

export function Answer({
  text,
  evidence,
  citations,
  figures = [],
  onOpenReference,
}: AnswerProps) {
  const plugin = useMemo(
    () => citationPlugin(evidence, citations),
    [evidence, citations],
  );
  const source = useMemo(() => normalizeMath(text), [text]);

  // A figure is placed after the paragraph that cites it, the way a textbook
  // sets one beside the passage discussing it — rather than collected into a
  // gallery at the end, where the reader has to work out which sentence each
  // one belongs to.
  const figuresByRank = useMemo(() => {
    const grouped = new Map<number, FigureRef[]>();
    for (const figure of figures) {
      if (figure.evidence_rank === null) continue;
      const existing = grouped.get(figure.evidence_rank);
      if (existing) existing.push(figure);
      else grouped.set(figure.evidence_rank, [figure]);
    }
    return grouped;
  }, [figures]);

  const rendered = new Set<number>();

  return (
    <div className="answer-prose">
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        // KaTeX runs before the citation pass so that markers are never
        // rewritten inside a rendered formula.
        rehypePlugins={[[rehypeKatex, { throwOnError: false }], plugin]}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          "citation-ref": ({ node }: any) => {
            const index = Number(node?.properties?.dataIndex ?? 0);
            return (
              <CitationChip
                index={index}
                reference={evidence[index - 1]}
                onOpen={onOpenReference}
              />
            );
          },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          p: ({ children, node }: any) => {
            // Which references does this paragraph cite? Its figures follow it.
            const ranks: number[] = [];
            const walk = (element: any) => {
              if (!element) return;
              if (element.tagName === "citation-ref") {
                const index = Number(element.properties?.dataIndex ?? 0);
                const reference = evidence[index - 1];
                if (reference?.rank != null) ranks.push(reference.rank);
              }
              (element.children ?? []).forEach(walk);
            };
            (node?.children ?? []).forEach(walk);

            const attached = ranks
              .filter((rank) => !rendered.has(rank))
              .flatMap((rank) => {
                rendered.add(rank);
                return figuresByRank.get(rank) ?? [];
              });

            return (
              <>
                <p>{children}</p>
                {attached.map((figure) => (
                  <InlineFigure key={figure.block_id} figure={figure} />
                ))}
              </>
            );
          },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
