"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { formatPages, formatPath, splitOnCitations } from "@/lib/citations";
import type { CitationRef, EvidenceRef } from "@/lib/types";
import { cn } from "@/lib/utils";

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
  const SKIP = new Set(["code", "pre"]);

  return () => (tree: HastNode) => {
    const walk = (node: HastNode) => {
      if (!hasChildren(node)) return;
      if (node.type === "element" && SKIP.has((node as HastElement).tagName)) {
        return;
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
  onOpenReference?: (reference: EvidenceRef) => void;
}

export function Answer({
  text,
  evidence,
  citations,
  onOpenReference,
}: AnswerProps) {
  const plugin = useMemo(
    () => citationPlugin(evidence, citations),
    [evidence, citations],
  );

  return (
    <div className="answer-prose">
      <ReactMarkdown
        rehypePlugins={[plugin]}
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
        } as any}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
