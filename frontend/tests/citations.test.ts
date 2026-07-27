import { describe, expect, it } from "vitest";

import {
  formatPages,
  formatPath,
  resolveMarker,
  splitOnCitations,
} from "@/lib/citations";
import type { CitationRef, EvidenceRef } from "@/lib/types";

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
