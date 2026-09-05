import { describe, expect, it } from "vitest";

import {
  buildNarrationScript,
  citedFigures,
  narrationChunks,
  splitSentences,
  CHUNK_CHARACTERS,
  FIRST_CHUNK_CHARACTERS,
} from "@/lib/narration";
import type { CitationRef, EvidenceRef, FigureRef } from "@/lib/types";

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
