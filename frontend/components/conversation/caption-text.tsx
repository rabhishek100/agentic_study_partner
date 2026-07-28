"use client";

import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import { normalizeMath } from "@/lib/math";

/**
 * A figure caption, with its maths rendered.
 *
 * Captions describe figures from statistics textbooks, so they routinely name
 * coefficients — 81 of the 692 in the corpus contain LaTeX. Rendered as plain
 * text they read as `$\beta_0$`, which is worse than useless in a caption
 * whose job is to say what the figure shows.
 *
 * Paragraphs are unwrapped: a caption is a fragment inside a `<figcaption>`,
 * not a document.
 */
export function CaptionText({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false }]]}
      components={{ p: ({ children: content }) => <>{content}</> }}
    >
      {normalizeMath(children)}
    </ReactMarkdown>
  );
}
