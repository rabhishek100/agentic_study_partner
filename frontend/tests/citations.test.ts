import { describe, expect, it } from "vitest";

import {
  ambiguousCitationPages,
  figuresForMarker,
  formatPages,
  formatPath,
  markerPage,
  resolveMarker,
  splitOnCitations,
} from "@/lib/citations";
import type { CitationRef, EvidenceRef, FigureRef } from "@/lib/types";

function evidence(overrides: Partial<EvidenceRef> = {}): EvidenceRef {
  return {
    node_id: 1,
    pages: [1],
    path: "Chapter 1",
    book_id: 7,
    book_title: "Sample Book",
    rank: null,
    chunk_id: null,
    chunk_index: null,
    retrieval_method: null,
    score: null,
    excerpt: null,
    ...overrides,
  };
}

function citation(overrides: Partial<CitationRef> = {}): CitationRef {
  return {
    marker: "[S1]",
    node_id: 1,
    page: 1,
    book_id: 7,
    evidence_rank: 1,
    ...overrides,
  };
}

describe("resolveMarker", () => {
  const retrieved = [
    evidence({ node_id: 10, rank: 1, path: "Chapter 1" }),
    evidence({ node_id: 20, rank: 2, path: "Chapter 2" }),
  ];

  it("maps a retrieval marker to its ranked evidence", () => {
    const resolved = resolveMarker("[S2]", retrieved, []);
    expect(resolved.index).toBe(2);
    expect(resolved.evidence?.node_id).toBe(20);
  });

  it("maps a node marker to the reference covering that page", () => {
    const summary = [
      evidence({ node_id: 10, pages: [1, 2] }),
      evidence({ node_id: 10, pages: [8, 9] }),
    ];
    const resolved = resolveMarker("[N10:P9]", summary, []);
    expect(resolved.index).toBe(2);
  });

  it("falls back to the node when no reference lists that exact page", () => {
    const summary = [evidence({ node_id: 10, pages: [1, 2] })];
    expect(resolveMarker("[N10:P77]", summary, []).index).toBe(1);
  });

  it("refuses to resolve a rank the server never returned", () => {
    // A model that invents [S9] against five documents must not produce a
    // chip pointing at nothing.
    const resolved = resolveMarker("[S9]", retrieved, []);
    expect(resolved.index).toBeNull();
    expect(resolved.evidence).toBeNull();
  });

  it("attaches the matching CitationRef when the server recorded one", () => {
    const resolved = resolveMarker("[S1]", retrieved, [citation()]);
    expect(resolved.citation?.marker).toBe("[S1]");
  });
});

describe("splitOnCitations", () => {
  const retrieved = [
    evidence({ node_id: 10, rank: 1 }),
    evidence({ node_id: 20, rank: 2 }),
  ];

  it("splits text around resolvable markers", () => {
    const segments = splitOnCitations(
      "Skew arises from drift [S1] and from stale features [S2].",
      retrieved,
      [],
    );

    expect(segments.map((segment) => segment.type)).toEqual([
      "text",
      "citation",
      "text",
      "citation",
      "text",
    ]);
    expect(segments[0]).toEqual({
      type: "text",
      value: "Skew arises from drift ",
    });
    expect(segments[3]).toMatchObject({ type: "citation", index: 2 });
    expect(segments[4]).toEqual({ type: "text", value: "." });
  });

  it("leaves an unresolvable marker as literal text", () => {
    const segments = splitOnCitations("Claimed [S9] here.", retrieved, []);
    expect(segments).toEqual([{ type: "text", value: "Claimed [S9] here." }]);
  });

  it("handles adjacent markers with no text between them", () => {
    const segments = splitOnCitations("Both apply [S1][S2]", retrieved, []);
    expect(segments.map((segment) => segment.type)).toEqual([
      "text",
      "citation",
      "citation",
    ]);
  });

  it("returns plain text unchanged when there are no markers", () => {
    expect(splitOnCitations("No citations here.", retrieved, [])).toEqual([
      { type: "text", value: "No citations here." },
    ]);
  });

  it("does not leak regex state between calls", () => {
    // CITATION_PATTERN carries the global flag; reusing it directly would
    // make the second call start scanning from the first call's offset.
    const once = splitOnCitations("a [S1] b", retrieved, []);
    const twice = splitOnCitations("a [S1] b", retrieved, []);
    expect(twice).toEqual(once);
  });

  it("handles both marker formats in one answer", () => {
    const mixed = [
      evidence({ node_id: 10, rank: 1 }),
      evidence({ node_id: 55, pages: [4] }),
    ];
    const segments = splitOnCitations("One [S1] two [N55:P4].", mixed, []);
    const chips = segments.filter((segment) => segment.type === "citation");
    expect(chips).toHaveLength(2);
    expect(chips.map((chip) => (chip as { index: number }).index)).toEqual([
      1, 2,
    ]);
  });
});

describe("formatting", () => {
  it("splits a canonical path into breadcrumb parts", () => {
    expect(formatPath("Chapter 1 :: Core idea :: Diagram")).toEqual([
      "Chapter 1",
      "Core idea",
      "Diagram",
    ]);
  });

  it("renders single and multi-page ranges", () => {
    expect(formatPages([12])).toBe("p. 12");
    expect(formatPages([12, 13, 14])).toBe("pp. 12–14");
    expect(formatPages([])).toBe("");
  });
});

describe("figuresForMarker", () => {
  function figure(overrides: Partial<FigureRef> = {}): FigureRef {
    return {
      book_id: 530,
      node_id: 3286,
      block_id: 1,
      page: 81,
      mime_type: "image/png",
      path: "3 Linear Regression :: 3.1 Simple Linear Regression",
      caption: "A scatter plot.",
      evidence_rank: null,
      ...overrides,
    };
  }

  it("attaches a retrieval figure by evidence rank", () => {
    const refs = [evidence({ node_id: 10, rank: 2 })];
    const marker = resolveMarker("[S2]", refs, []);
    const figures = [figure({ node_id: 10, evidence_rank: 2, block_id: 7 })];

    expect(figuresForMarker(figures, marker).map((f) => f.block_id)).toEqual([7]);
  });

  it("attaches a summary figure by node and page", () => {
    // The real shape: a summary's evidence has no rank at all, so keying on
    // rank alone excluded every one of these.
    const refs = [evidence({ node_id: 3286, pages: [81, 82, 83], rank: null })];
    const cites = [
      citation({ marker: "[N3286:P82]", node_id: 3286, page: 82, evidence_rank: null }),
    ];
    const marker = resolveMarker("[N3286:P82]", refs, cites);
    const figures = [
      figure({ page: 81, block_id: 1 }),
      figure({ page: 82, block_id: 2 }),
    ];

    expect(figuresForMarker(figures, marker).map((f) => f.block_id)).toEqual([2]);
  });

  it("does not attach a figure from a different node", () => {
    const refs = [evidence({ node_id: 3286, pages: [81], rank: null })];
    const cites = [
      citation({ marker: "[N3286:P81]", node_id: 3286, page: 81, evidence_rank: null }),
    ];
    const marker = resolveMarker("[N3286:P81]", refs, cites);

    expect(figuresForMarker([figure({ node_id: 999 })], marker)).toEqual([]);
  });

  it("falls back to the node's evidence pages when the citation is absent", () => {
    const refs = [evidence({ node_id: 3286, pages: [81, 82], rank: null })];
    const marker = resolveMarker("[N3286:P82]", refs, []);

    expect(figuresForMarker([figure({ page: 82 })], marker)).toHaveLength(1);
  });
});

describe("markerPage", () => {
  it("prefers the page the marker itself names", () => {
    // A summary's evidence spans several pages; opening its first would land
    // the reader well away from the sentence they clicked.
    const refs = [evidence({ node_id: 3286, pages: [81, 82, 83, 84, 85] })];
    const cites = [
      citation({ marker: "[N3286:P84]", node_id: 3286, page: 84, evidence_rank: null }),
    ];

    expect(markerPage(resolveMarker("[N3286:P84]", refs, cites))).toBe(84);
  });

  it("falls back to the reference's first page", () => {
    const refs = [evidence({ node_id: 10, pages: [12, 13], rank: 1 })];
    expect(markerPage(resolveMarker("[S1]", refs, []))).toBe(12);
  });

  it("returns null when nothing resolves", () => {
    expect(markerPage(resolveMarker("[S9]", [], []))).toBeNull();
  });
});


describe("ambiguousCitationPages", () => {
  it("shows the page when one source is cited at several of them", () => {
    // A chapter summary of a book whose table of contents stops at the
    // chapter: every citation names the same node, so every chip was "1".
    const citations = [
      citation({ marker: "[N2531:P5]", node_id: 2531, page: 5 }),
      citation({ marker: "[N2531:P22]", node_id: 2531, page: 22 }),
      citation({ marker: "[N2531:P32]", node_id: 2531, page: 32 }),
    ];

    const shown = ambiguousCitationPages(citations);

    expect(shown.get("[N2531:P5]")).toBe(5);
    expect(shown.get("[N2531:P22]")).toBe(22);
    expect(shown.get("[N2531:P32]")).toBe(32);
  });

  it("leaves a source cited once with its bare number", () => {
    const citations = [
      citation({ marker: "[N10:P3]", node_id: 10, page: 3 }),
      citation({ marker: "[N11:P9]", node_id: 11, page: 9 }),
    ];

    expect(ambiguousCitationPages(citations).size).toBe(0);
  });

  it("disambiguates only the source that needs it", () => {
    const citations = [
      citation({ marker: "[N10:P3]", node_id: 10, page: 3 }),
      citation({ marker: "[N11:P9]", node_id: 11, page: 9 }),
      citation({ marker: "[N11:P14]", node_id: 11, page: 14 }),
    ];

    const shown = ambiguousCitationPages(citations);

    expect(shown.has("[N10:P3]")).toBe(false);
    expect(shown.get("[N11:P9]")).toBe(9);
    expect(shown.get("[N11:P14]")).toBe(14);
  });

  it("treats one page cited repeatedly as unambiguous", () => {
    const citations = [
      citation({ marker: "[N7:P4]", node_id: 7, page: 4 }),
      citation({ marker: "[N7:P4]", node_id: 7, page: 4 }),
    ];

    expect(ambiguousCitationPages(citations).size).toBe(0);
  });

  it("ignores retrieval markers, which carry no node or page", () => {
    // [S1] markers are ordinals into the retrieved evidence; their number is
    // the locator, so there is nothing to disambiguate.
    const shown = ambiguousCitationPages([
      citation({ marker: "[S1]", node_id: undefined as never, page: undefined as never }),
      citation({ marker: "[S2]", node_id: undefined as never, page: undefined as never }),
    ]);

    expect(shown.size).toBe(0);
  });
});
