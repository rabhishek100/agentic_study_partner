import { describe, expect, it } from "vitest";

import {
  buildNarrationScript,
  buildPassageNarrationScript,
  citedFigures,
  narrationChunks,
  narrationItems,
  pronunciationText,
  splitSentences,
  CHUNK_CHARACTERS,
  FIRST_CHUNK_CHARACTERS,
} from "@/lib/narration";
import type { CitationRef, EvidenceRef, FigureRef, PassageSegment, ReadingRef } from "@/lib/types";

function evidence(rank: number, nodeId = 7, pages = [84]): EvidenceRef {
  return {
    node_id: nodeId,
    pages,
    path: "Chapter 3 :: Regularisation",
    book_id: 1,
    book_title: "Sample Book",
    rank,
    chunk_id: null,
    chunk_index: null,
    retrieval_method: null,
    score: null,
    excerpt: null,
  };
}

function figure(blockId: number, page = 84, rank: number | null = 1): FigureRef {
  return {
    book_id: 1,
    node_id: 7,
    block_id: blockId,
    page,
    mime_type: "image/png",
    path: "Chapter 3 :: Regularisation",
    caption: "Validation error against model complexity.",
    evidence_rank: rank,
  };
}

/** The whole script as one string, for assertions about what is said. */
function spoken(script: { segments: { text: string }[] }): string {
  return script.segments.map((segment) => segment.text).join(" ");
}

describe("what the voice is given to say", () => {
  it("strips markdown rather than spelling it out", () => {
    const script = buildNarrationScript({
      answer: "## Overfitting\n\nA model that is **too flexible** fits `noise`.",
    });

    const text = spoken(script);
    expect(text).toContain("Overfitting.");
    expect(text).toContain("too flexible");
    expect(text).toContain("noise");
    expect(text).not.toContain("**");
    expect(text).not.toContain("#");
    expect(text).not.toContain("`");
  });

  it("removes raw formatting controls that a voice would read as gibberish", () => {
    const script = buildNarrationScript({
      answer: "- [x] **Ship it** &amp; verify it.\n\n<span>Hidden markup</span>",
    });

    const text = spoken(script);
    expect(text).toContain("Ship it and verify it.");
    expect(text).toContain("Hidden markup.");
    expect(text).not.toMatch(/\[x\]|span|amp/i);
  });

  it("says maths in words", () => {
    const script = buildNarrationScript({
      answer: "The rule of thumb is $m \\approx \\sqrt{p}$ features.",
    });

    expect(spoken(script)).toContain("m approximately the square root of p");
    expect(spoken(script)).not.toContain("\\");
  });

  it("does not read citation markers out loud", () => {
    const script = buildNarrationScript({
      answer: "Regularisation shrinks the coefficients [S1].",
      evidence: [evidence(1)],
      citations: [],
    });

    const text = spoken(script);
    expect(text).toContain("Regularisation shrinks the coefficients.");
    expect(text).not.toContain("S1");
    // The marker's space goes with it; "coefficients ." would be a stumble.
    expect(text).not.toContain(" .");
  });

  it("announces a code block instead of reading it character by character", () => {
    const script = buildNarrationScript({
      answer: "Fit it like this:\n\n```python\nmodel.fit(X, y)\nmodel.score(X, y)\n```\n\nThen check the score.",
    });

    const text = spoken(script);
    expect(text).toContain("python code block, 2 lines, shown on screen.");
    expect(text).not.toContain("model.fit");
    expect(text).toContain("Then check the score.");
  });

  it("announces a table by its size", () => {
    const script = buildNarrationScript({
      answer: "| Model | Error |\n| --- | --- |\n| Ridge | 0.21 |\n| Lasso | 0.24 |",
    });

    expect(spoken(script)).toContain("Table with 2 rows, shown on screen.");
  });

  it("describes a cited figure right after the sentence that cites it", () => {
    const script = buildNarrationScript({
      answer:
        "Error falls and then rises [S1]. That turning point is the bias-variance trade-off.",
      evidence: [evidence(1)],
      figures: [figure(42)],
      descriptions: { 42: "Validation error dips at moderate complexity and climbs after it" },
    });

    const texts = script.segments.map((segment) => segment.text);
    expect(texts[0]).toContain("Error falls and then rises.");
    expect(texts[1]).toContain("Figure, page 84.");
    expect(texts[1]).toContain("Validation error dips at moderate complexity");
    // The next sentence follows the figure, not the other way round.
    expect(texts[2]).toContain("bias-variance trade-off");
  });

  it("keeps prose between figures instead of batching the images", () => {
    const script = buildNarrationScript({
      answer: "First diagram [S1].\n\nImportant text between them.\n\nSecond diagram [S2].",
      evidence: [evidence(1, 7, [84]), evidence(2, 8, [85])],
      figures: [figure(42, 84, 1), { ...figure(43, 85, 2), node_id: 8 }],
      descriptions: { 42: "The first flow.", 43: "The second flow." },
    });

    expect(script.segments.map((segment) => segment.kind)).toEqual([
      "prose", "figure", "prose", "prose", "figure",
    ]);
    expect(script.segments[2]!.text).toContain("Important text between them");
  });

  it("still announces a figure whose description could not be produced", () => {
    const script = buildNarrationScript({
      answer: "Error falls and then rises [S1].",
      evidence: [evidence(1)],
      figures: [figure(42)],
      descriptions: {},
    });

    expect(spoken(script)).toContain("Figure, page 84.");
  });

  it("speaks a figure once even when two sentences cite it", () => {
    const script = buildNarrationScript({
      answer: "Error falls [S1]. It then rises [S1].",
      evidence: [evidence(1)],
      figures: [figure(42)],
      descriptions: { 42: "A U-shaped curve" },
    });

    const mentions = script.segments.filter((segment) => segment.kind === "figure");
    expect(mentions).toHaveLength(1);
  });

  it("speaks figures no sentence claimed, after the prose", () => {
    const script = buildNarrationScript({
      answer: "Regularisation shrinks the coefficients.",
      figures: [figure(42, 84, null)],
      descriptions: { 42: "A shrinkage path" },
    });

    const texts = script.segments.map((segment) => segment.text);
    expect(texts.at(-2)).toBe("Also shown.");
    expect(texts.at(-1)).toContain("A shrinkage path.");
  });

  it("speaks a lecture frame's own summary without describing it again", () => {
    const script = buildNarrationScript({
      answer: "The learning rate controls step size.",
      visuals: [{ lead: "At 12:04", description: "Three loss curves for three learning rates" }],
    });

    expect(spoken(script)).toContain("At 12:04. Three loss curves for three learning rates.");
  });

  it("reads the question first when the exchange is what was asked for", () => {
    const script = buildNarrationScript({
      question: "Why does regularisation help?",
      answer: "It trades variance for bias.",
    });

    expect(script.segments[0]!.text).toBe("You asked: Why does regularisation help?");
    expect(script.segments[0]!.anchor).toEqual({ type: "question", sentence: 0 });
  });

  it("reports the figures it will reach, so their descriptions can be fetched", () => {
    const input = {
      answer: "Error falls [S1].",
      evidence: [evidence(1)],
      figures: [figure(42)],
    };

    expect(citedFigures(input).map((found) => found.block_id)).toEqual([42]);
  });
});

describe("splitting for the synthesiser", () => {
  it("keeps sentence boundaries addressable for configurable silence", () => {
    const items = narrationItems(
      buildNarrationScript({ answer: "First idea. Second idea follows." }),
    );

    expect(items.map((item) => item.text)).toEqual([
      "First idea.",
      "Second idea follows.",
    ]);
    expect(items.map((item) => item.pauseAfter)).toEqual(["sentence", "paragraph"]);
  });

  it("keeps stable source anchors for headings, prose and figures", () => {
    const items = narrationItems(buildNarrationScript({
      answer: "## Architecture\n\nThe API calls an LLM [S1].",
      evidence: [evidence(1)],
      figures: [figure(42)],
      descriptions: { 42: "A request flow." },
    }));

    expect(items[0]!.anchor).toEqual({ type: "block", key: "line-1" });
    expect(items[1]!.anchor).toEqual({ type: "block", key: "line-3", sentence: 0 });
    expect(items[2]!.anchor).toEqual({ type: "figure", blockId: 42 });
  });

  it("keeps wrapped list lines with their rendered list anchor", () => {
    const items = narrationItems(buildNarrationScript({
      answer: "- First item continues\n  on a wrapped line\n- Second item",
    }));

    expect(items.map((item) => item.text)).toEqual([
      "First item continues on a wrapped line.",
      "Second item.",
    ]);
    expect(items.map((item) => item.anchor)).toEqual([
      { type: "block", key: "line-1", sentence: 0 },
      { type: "block", key: "line-1", sentence: 1 },
    ]);
  });

  it("reads a numbered heading as one highlighted utterance", () => {
    const items = narrationItems(buildNarrationScript({
      answer: "## 1. Clarify scope and quantify the workload",
    }));

    expect(items).toHaveLength(1);
    expect(items[0]!.text).toBe("1. Clarify scope and quantify the workload.");
    expect(items[0]!.anchor).toEqual({ type: "block", key: "line-1" });
  });

  it("adds conservative pronunciation hints for technical text", () => {
    expect(
      pronunciationText("The API sends BM25 results to an LLM via HTTP."),
    ).toBe("The A P I sends B M 25 results to an L L M via H T T P.");
    expect(pronunciationText("RAG uses JSON and CUDA.")).toBe(
      "RAG uses JSON and CUDA.",
    );
    expect(pronunciationText("Set max_tokens in getUserID.")).toBe(
      "Set max tokens in get User ID.",
    );
  });

  it("keeps an abbreviation's full stop inside its sentence", () => {
    const sentences = splitSentences(
      "The residuals are shown in Fig. 4. They are unpatterned.",
    );

    expect(sentences).toEqual([
      "The residuals are shown in Fig. 4.",
      "They are unpatterned.",
    ]);
  });

  it("does not split a decimal", () => {
    expect(splitSentences("R squared is 0.72 here.")).toHaveLength(1);
  });

  it("starts with a short chunk so the first sound arrives quickly", () => {
    const script = buildNarrationScript({
      answer: Array.from({ length: 40 }, (_, index) => `Sentence number ${index}.`).join(" "),
    });

    const chunks = narrationChunks(script);

    expect(chunks[0]!.length).toBeLessThanOrEqual(FIRST_CHUNK_CHARACTERS);
    expect(chunks.length).toBeGreaterThan(1);
    for (const chunk of chunks.slice(1)) {
      expect(chunk.length).toBeLessThanOrEqual(CHUNK_CHARACTERS);
    }
  });

  it("cuts only at sentence ends, because a chunk boundary is heard", () => {
    const script = buildNarrationScript({
      answer: Array.from({ length: 30 }, (_, index) => `This is sentence ${index}.`).join(" "),
    });

    for (const chunk of narrationChunks(script)) {
      expect(chunk.trim()).toMatch(/[.!?]$/);
    }
  });

  it("drops a chunk with nothing sayable in it", () => {
    const script = buildNarrationScript({ answer: "---\n\n***\n" });

    expect(narrationChunks(script)).toEqual([]);
  });
});

describe("complete chapter narration", () => {
  const reading: ReadingRef = {
    book_id: 1,
    book_title: "ML Systems",
    node_id: 12,
    kind: "chapter",
    display_path: "Chapter 1. Machine Learning Systems",
    start_page: 1,
    end_page: 20,
    printed_start_page: 1,
    printed_end_page: 20,
    total_segments: 6,
    total_characters: 200,
    omitted_block_count: 0,
  };
  const segment = (
    index: number,
    kind: PassageSegment["kind"],
    text: string | null,
  ): PassageSegment => ({
    index,
    kind,
    text,
    node_id: 12,
    page: 1,
    printed_page: 1,
    level: kind === "heading" ? 2 : null,
    html: null,
    figure: null,
  });

  it("reads the title once and preserves section rhythm and source anchors", () => {
    const script = buildPassageNarrationScript({
      reading,
      segments: [
        segment(0, "heading", "Chapter 1. Machine Learning Systems"),
        segment(1, "heading", "System requirements"),
        segment(2, "text", "First thought. Second thought."),
      ],
    });
    const items = narrationItems(script);

    expect(items.map((item) => item.text)).toEqual([
      "Chapter 1. Machine Learning Systems.",
      "System requirements.",
      "First thought.",
      "Second thought.",
    ]);
    expect(items.map((item) => item.pauseAfter)).toEqual([
      "title", "heading", "sentence", "paragraph",
    ]);
    expect(items[1]!.anchor).toEqual({ type: "passage", index: 1 });
  });

  it("removes retained formatting before speech and announces visual structures", () => {
    const script = buildPassageNarrationScript({
      reading,
      segments: [
        segment(0, "text", "**Training.** <span>Use</span> $x^2$ samples &amp; verify."),
        segment(1, "list_item", "3. Set `max_tokens`."),
        segment(2, "table", "| raw | markdown |"),
      ],
    });
    const text = narrationItems(script).map((item) => item.speechText).join(" ");

    expect(text).toContain("Training. Use x squared samples and verify.");
    expect(text).toContain("Set max tokens.");
    expect(text).toContain("Table, shown on screen.");
    expect(text).not.toMatch(/\*\*|<span>|`|\$|\|/);
  });
});
