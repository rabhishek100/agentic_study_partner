"use client";

import { FileText, Film, ImageIcon, Quote } from "lucide-react";
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
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

interface InlineFrameRef {
  frameId: number;
  startMs: number;
  caption: string;
}

interface VideoAnswerProps {
  videoId: string;
  answer: string;
  evidence: VideoEvidenceRef[];
  citations: VideoCitationRef[];
  onSeek(milliseconds: number): void;
  onOpenDocument(target: VideoDocumentTarget): void;
}

/**
 * The frames an answer cites, which are the ones shown beside the prose.
 *
 * Exported so the turn can leave them out of the grid underneath: a screenshot
 * shown next to its claim and again in a strip below is the same picture
 * twice, and the second one teaches the reader to ignore both.
 */
export function citedFrameIds(citations: VideoCitationRef[]): Set<number> {
  return new Set(
    citations
      .filter((item) => item.modality === "visual_frame" && item.frame_id)
      .map((item) => item.frame_id as number),
  );
}

/** Minimal hast shapes, enough to rewrite text nodes without a tree library. */
type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

/** Every citation marker inside a block, in the order they appear. */
function markersWithin(node: HastNode, found: string[] = []): string[] {
  if (node.tagName === "citation-ref") {
    found.push(String(node.properties?.marker ?? ""));
  }
  for (const child of node.children ?? []) markersWithin(child, found);
  return found;
}

/**
 * Put each cited frame after the block that cites it.
 *
 * The frames were already shown, as a four-up grid under the finished answer —
 * which means a screenshot described in the first paragraph sat below the
 * fifth, and the reader had to work out which picture went with which claim.
 * A figure belongs next to the sentence it illustrates, the way a textbook
 * sets one.
 *
 * Only at the top level: a figure inserted after a list item would break out
 * of its list, so a frame cited inside a list appears once the list ends.
 * Each frame renders once, at its first mention, because the same slide cited
 * three times is one picture, not three.
 */
function inlineFramePlugin(frames: Map<string, InlineFrameRef>) {
  return () => (tree: HastNode) => {
    if (!tree.children || frames.size === 0) return;
    const shown = new Set<number>();
    const rebuilt: HastNode[] = [];
    for (const block of tree.children) {
      rebuilt.push(block);
      for (const marker of markersWithin(block)) {
        const frame = frames.get(marker);
        if (!frame || shown.has(frame.frameId)) continue;
        shown.add(frame.frameId);
        rebuilt.push({
          type: "element",
          tagName: "frame-figure",
          properties: {
            frame: frame.frameId,
            at: frame.startMs,
            caption: frame.caption,
          },
          children: [],
        });
      }
    }
    tree.children = rebuilt;
  };
}

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
 * One cited frame, shown where the answer talks about it.
 *
 * Narrower than the prose and clickable, so it reads as an illustration of the
 * claim above rather than as the answer itself. A frame that will not load
 * renders nothing at all: an empty box between two paragraphs interrupts the
 * reading to report a problem the reader cannot do anything about, and the
 * citation chip above it still seeks the moment.
 */
function InlineFrame({
  videoId,
  frameId,
  startMs,
  caption,
  onSeek,
}: {
  videoId: string;
  frameId: number;
  startMs: number;
  caption: string;
  onSeek(milliseconds: number): void;
}) {
  const image = useAuthenticatedImage(
    `/api/videos/${videoId}/frames/${frameId}/image`,
  );
  if (image.status === "failed") return null;
  return (
    <figure className="my-3 max-w-md">
      <button
        type="button"
        onClick={() => onSeek(startMs)}
        className="block w-full rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Play from ${formatTimestamp(startMs)}: ${caption}`}
      >
        {image.status === "loading" ? (
          <Skeleton className="aspect-video w-full rounded-md" />
        ) : (
          // The endpoint needs a bearer token, so the bytes arrive as an object
          // URL rather than a plain src the optimizer could handle.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={image.url}
            alt={caption}
            className="aspect-video w-full rounded-md border border-border object-cover"
          />
        )}
      </button>
      <figcaption className="mt-1 flex gap-1.5 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">
          {formatTimestamp(startMs)}
        </span>
        <span className="line-clamp-2">{caption}</span>
      </figcaption>
    </figure>
  );
}

/**
 * Render the answer as markdown, with its markers turned into controls.
 *
 * A timestamp marker seeks the player; a page marker opens that page. The two
 * are never merged into one "source" chip, because the lecture and the slides
 * are different places and the answer is only allowed to claim what it cited.
 */
export function VideoAnswer({
  videoId,
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
  const framesByMarker = useMemo(() => {
    const frames = new Map<string, InlineFrameRef>();
    for (const citation of citations) {
      if (citation.modality !== "visual_frame" || !citation.frame_id) continue;
      const item = evidence.find((row) => row.rank === citation.evidence_rank);
      frames.set(citation.marker, {
        frameId: citation.frame_id,
        startMs: citation.start_ms ?? 0,
        // The frame's own description, which is what the model was shown. It
        // is the honest caption: anything written here instead would be the
        // interface describing a picture it cannot see.
        caption: item?.excerpt?.slice(0, 240) ?? "Frame from the lecture",
      });
    }
    return frames;
  }, [citations, evidence]);

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
      // Marks the prose as a passage a reader may highlight and anchor a side
      // chat to. Without it the selection popover refuses every lecture
      // selection, because reference cards and controls are not passages.
      data-answer=""
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
        rehypePlugins={[
          rehypeKatex,
          citationPlugin(),
          inlineFramePlugin(framesByMarker),
        ]}
        components={
          {
            "citation-ref": ({ marker }: { marker: string }) =>
              renderCitation(marker),
            "frame-figure": ({
              frame,
              at,
              caption,
            }: {
              frame: string | number;
              at: string | number;
              caption: string;
            }) => (
              <InlineFrame
                videoId={videoId}
                frameId={Number(frame)}
                startMs={Number(at)}
                caption={caption}
                onSeek={onSeek}
              />
            ),
          } as never
        }
      >
        {rendered}
      </ReactMarkdown>
    </div>
  );
}
