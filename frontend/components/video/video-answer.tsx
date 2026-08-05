"use client";

import { FileText, Film, ImageIcon, Quote } from "lucide-react";
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { normalizeMath } from "@/lib/math";
import { cn } from "@/lib/utils";

import "katex/dist/katex.min.css";
import {
  formatTimestamp,
  type VideoCitationRef,
  type VideoDocumentTarget,
  type VideoEvidenceRef,
  type VideoModality,
} from "@/lib/video-types";

const MARKER = /\[S(\d+)]/g;

const MODALITY_ICON: Record<VideoModality, typeof Film> = {
  transcript: Quote,
  visual_frame: ImageIcon,
  visual_event: Film,
  resource_page: FileText,
};

export function citationLabel(
  citation: VideoCitationRef,
  evidence?: VideoEvidenceRef,
): string {
  if (citation.modality === "resource_page") {
    const document = evidence?.resource_title ?? "Document";
    return `${document} p. ${citation.page_number ?? "?"}`;
  }
  return formatTimestamp(citation.start_ms);
}

interface VideoAnswerProps {
  answer: string;
  evidence: VideoEvidenceRef[];
  citations: VideoCitationRef[];
  onSeek(milliseconds: number): void;
  onOpenDocument(target: VideoDocumentTarget): void;
}

/** Minimal hast shapes, enough to rewrite text nodes without a tree library. */
type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

/**
 * Turn citation markers in the rendered tree into elements the renderer can
 * swap for controls.
 *
 * Done as a rehype pass rather than a string replace so markers inside code
 * spans and formulas are left alone — the answer sometimes quotes them, and a
 * button inside a code sample would be wrong.
 */
function citationPlugin() {
  const SKIP = new Set(["code", "pre", "math", "semantics", "annotation"]);
  return () => (tree: HastNode) => {
    const walk = (node: HastNode) => {
      if (!node.children) return;
      const rebuilt: HastNode[] = [];
      for (const child of node.children) {
        if (child.type === "element" && SKIP.has(child.tagName ?? "")) {
          rebuilt.push(child);
          continue;
        }
        if (child.type !== "text" || !child.value) {
          walk(child);
          rebuilt.push(child);
          continue;
        }
        let index = 0;
        for (const match of child.value.matchAll(MARKER)) {
          const at = match.index ?? 0;
          if (at > index) {
            rebuilt.push({ type: "text", value: child.value.slice(index, at) });
          }
          rebuilt.push({
            type: "element",
            tagName: "citation-ref",
            properties: { marker: match[0] },
            children: [],
          });
          index = at + match[0].length;
        }
        if (index === 0) rebuilt.push(child);
        else if (index < child.value.length) {
          rebuilt.push({ type: "text", value: child.value.slice(index) });
        }
      }
      node.children = rebuilt;
    };
    walk(tree);
  };
}

/**
 * Render the answer as markdown, with its markers turned into controls.
 *
 * A timestamp marker seeks the player; a page marker opens that page. The two
 * are never merged into one "source" chip, because the lecture and the slides
 * are different places and the answer is only allowed to claim what it cited.
 */
export function VideoAnswer({
  answer,
  evidence,
  citations,
  onSeek,
  onOpenDocument,
}: VideoAnswerProps) {
  const byRank = useMemo(
    () => new Map(evidence.map((item) => [item.rank, item])),
    [evidence],
  );
  const citationByMarker = useMemo(
    () => new Map(citations.map((item) => [item.marker, item])),
    [citations],
  );

  const rendered = useMemo(() => normalizeMath(answer), [answer]);

  const renderCitation = (marker: string) => {
    const citation = citationByMarker.get(marker);
    if (!citation) return <span>{marker}</span>;
    const item = byRank.get(citation.evidence_rank);
    const Icon = MODALITY_ICON[citation.modality];
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={cn(
              "mx-0.5 h-6 gap-1 rounded-full border border-border bg-muted/60 px-2",
              "align-baseline text-xs font-normal",
            )}
            onClick={() =>
              citation.modality === "resource_page" && citation.resource_id
                ? onOpenDocument({
                    resourceId: citation.resource_id,
                    page: citation.page_number ?? 1,
                    // The passage the viewer highlights comes from the
                    // evidence, not the citation: the marker names a page,
                    // and the page is not the claim.
                    excerpt: item?.excerpt,
                  })
                : onSeek(citation.start_ms ?? 0)
            }
          >
            <Icon aria-hidden className="size-3" />
            {citationLabel(citation, item)}
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-sm">
          <span className="line-clamp-4 text-xs">
            {item?.excerpt ?? "Cited evidence"}
          </span>
        </TooltipContent>
      </Tooltip>
    );
  };

  return (
    <div
      className={cn(
        "text-sm leading-relaxed",
        "[&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5",
        "[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5",
        "[&_strong]:font-semibold [&_h1]:text-base [&_h2]:text-base",
        "[&_h3]:text-sm [&_h1]:font-medium [&_h2]:font-medium",
        "[&_h3]:font-medium [&_code]:rounded [&_code]:bg-muted [&_code]:px-1",
        "[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3",
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex, citationPlugin()]}
        components={
          {
            "citation-ref": ({ marker }: { marker: string }) =>
              renderCitation(marker),
          } as never
        }
      >
        {rendered}
      </ReactMarkdown>
    </div>
  );
}
