import { describe, expect, it } from "vitest";

import {
  commonPathPrefix,
  groupByBook,
  partitionByCitation,
  pathBelow,
} from "@/lib/references";
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

const ISLP_PREFIX = "8 Tree-Based Methods :: 8.2 Bagging, Random Forests";

describe("commonPathPrefix", () => {
  it("lifts the ancestry shared by every reference", () => {
    expect(
      commonPathPrefix([
        ["8 Tree-Based Methods", "8.2 Bagging", "8.2.2 Random Forests"],
        ["8 Tree-Based Methods", "8.2 Bagging", "8.2.4 BART"],
      ]),
    ).toEqual(["8 Tree-Based Methods", "8.2 Bagging"]);
  });

  it("stops at the first divergence", () => {
    expect(
      commonPathPrefix([
        ["8 Tree-Based Methods", "8.2 Bagging", "8.2.2 Random Forests"],
        ["8 Tree-Based Methods", "8.1 Basics"],
      ]),
    ).toEqual(["8 Tree-Based Methods"]);
  });

  it("never consumes a reference's own leaf", () => {
    // The second path IS the prefix; promoting it would leave that row with
    // nothing to identify it.
    expect(
      commonPathPrefix([
        ["8 Tree-Based Methods", "8.2 Bagging"],
        ["8 Tree-Based Methods"],
      ]),
    ).toEqual([]);
  });

  it("lifts everything above the leaf for a single reference", () => {
    expect(commonPathPrefix([["A", "B", "C"]])).toEqual(["A", "B"]);
  });

  it("lifts nothing from a single reference that is already a leaf", () => {
    expect(commonPathPrefix([["A"]])).toEqual([]);
  });

  it("returns nothing when the references share no ancestor", () => {
    expect(commonPathPrefix([["A", "B"], ["C", "D"]])).toEqual([]);
  });

  it("handles an empty list", () => {
    expect(commonPathPrefix([])).toEqual([]);
  });
});

describe("pathBelow", () => {
  it("returns the part under the shared prefix", () => {
    expect(pathBelow(`${ISLP_PREFIX} :: 8.2.2 Random Forests`, [
      "8 Tree-Based Methods",
      "8.2 Bagging, Random Forests",
    ])).toEqual(["8.2.2 Random Forests"]);
  });

  it("returns the whole path when there is no prefix", () => {
    expect(pathBelow("A :: B", [])).toEqual(["A", "B"]);
  });
});

describe("groupByBook", () => {
  it("groups by book and computes each book's shared path", () => {
    const groups = groupByBook([
      evidence({ book_id: 1, book_title: "ISLP", path: "Ch 8 :: 8.2 :: RF" }),
      evidence({ book_id: 1, book_title: "ISLP", path: "Ch 8 :: 8.2 :: BART" }),
      evidence({ book_id: 2, book_title: "MLE", path: "Ch 4 :: Shift" }),
    ]);

    expect(groups).toHaveLength(2);
    expect(groups[0]?.bookTitle).toBe("ISLP");
    expect(groups[0]?.sharedPath).toEqual(["Ch 8", "8.2"]);
    // A lone reference lifts its ancestry too, leaving just the leaf on the row.
    expect(groups[1]?.sharedPath).toEqual(["Ch 4"]);
  });

  it("keeps books with no identity distinguishable", () => {
    const groups = groupByBook([
      evidence({ book_id: null, book_title: null, path: "A" }),
    ]);
    expect(groups[0]?.bookTitle).toBe("This book");
  });
});

describe("partitionByCitation", () => {
  const retrieved = [
    evidence({ node_id: 10, rank: 1 }),
    evidence({ node_id: 20, rank: 2 }),
    evidence({ node_id: 30, rank: 3 }),
  ];

  function citation(overrides: Partial<CitationRef> = {}): CitationRef {
    return {
      marker: "[S1]",
      node_id: 10,
      page: 1,
      book_id: 7,
      evidence_rank: 1,
      ...overrides,
    };
  }

  it("separates cited references from merely retrieved ones", () => {
    const { cited, uncited } = partitionByCitation(retrieved, [citation()]);
    expect(cited.map((entry) => entry.node_id)).toEqual([10]);
    expect(uncited.map((entry) => entry.node_id)).toEqual([20, 30]);
  });

  it("matches summary citations by node when there is no rank", () => {
    const { cited } = partitionByCitation(retrieved, [
      citation({ marker: "[N30:P5]", node_id: 30, evidence_rank: null }),
    ]);
    expect(cited.map((entry) => entry.node_id)).toEqual([30]);
  });

  it("treats everything as cited when the turn recorded no citations", () => {
    // An abstention or an uncited summary should not hide all its evidence.
    const { cited, uncited } = partitionByCitation(retrieved, []);
    expect(cited).toHaveLength(3);
    expect(uncited).toHaveLength(0);
  });

  it("preserves retrieval order within each partition", () => {
    const { uncited } = partitionByCitation(retrieved, [citation()]);
    expect(uncited.map((entry) => entry.rank)).toEqual([2, 3]);
  });
});
