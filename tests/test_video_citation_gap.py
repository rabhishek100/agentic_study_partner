"""The three shapes a cited-recall shortfall takes.

Cited-evidence recall has trailed retrieval recall since the video gold set
was built, and the gap was read as one thing: the answer citing fewer
stretches than it reached. Classifying the shortfall says otherwise — on the
measured run not one anchor was missed for want of a citation, and the number
is mostly reporting where the markers landed rather than whether they exist.
"""

import unittest

from evals.video_citations import (
    ADJACENT_TOLERANCE_MS,
    CITED_ADJACENT,
    CITED_ELSEWHERE,
    UNCITED,
    classify_citation_gaps,
    classify_turn,
)


def turn(*, evidence, citations, anchors, turn_id="t1", route="evidence_qa"):
    return {
        "turn_id": turn_id,
        "gold": {"expected_evidence": anchors},
        "prediction": {
            "route": route,
            "evidence": evidence,
            "citations": citations,
        },
    }


def item(rank, start_ms, *, modality="visual_frame", end_ms=None, page=None):
    return {
        "rank": rank,
        "modality": modality,
        "start_ms": start_ms,
        "end_ms": end_ms if end_ms is not None else start_ms,
        "page_number": page,
    }


def anchor(start_ms, end_ms, *, modalities=("visual_frame",), pages=None):
    body = {
        "role": "required",
        "modalities": list(modalities),
        "start_ms": start_ms,
        "end_ms": end_ms,
    }
    if pages is not None:
        body["resource_pages"] = pages
    return body


class CitationGapTests(unittest.TestCase):
    def test_a_cited_anchor_is_not_a_gap(self):
        row = turn(
            evidence=[item(1, 1_000)],
            citations=[{"evidence_rank": 1}],
            anchors=[anchor(500, 2_000)],
        )
        self.assertEqual(classify_turn(row), [])

    def test_an_anchor_retrieval_never_reached_is_not_a_gap(self):
        """Retrieval recall already counts that miss; counting it twice would
        make the citation number a worse copy of the recall number."""

        row = turn(
            evidence=[item(1, 90_000)],
            citations=[{"evidence_rank": 1}],
            anchors=[anchor(500, 2_000)],
        )
        self.assertEqual(classify_turn(row), [])

    def test_an_answer_that_cited_nothing_is_the_discipline_failure(self):
        row = turn(
            evidence=[item(1, 1_000)],
            citations=[],
            anchors=[anchor(500, 2_000)],
        )
        (gap,) = classify_turn(row)
        self.assertEqual(gap.kind, UNCITED)

    def test_the_same_slide_one_frame_later_is_adjacent_not_elsewhere(self):
        """A deck does not change between samples but the frame id does, and
        an anchor cut on the clock cannot tell those apart."""

        row = turn(
            evidence=[item(1, 130_000), item(2, 90_000)],
            citations=[{"evidence_rank": 1}],
            anchors=[anchor(60_000, 120_000)],
        )
        (gap,) = classify_turn(row)
        self.assertEqual(gap.kind, CITED_ADJACENT)
        self.assertEqual(gap.nearest_citation_ms, 10_000)

    def test_a_citation_past_the_tolerance_is_elsewhere(self):
        row = turn(
            evidence=[item(1, 120_000 + ADJACENT_TOLERANCE_MS + 1), item(2, 90_000)],
            citations=[{"evidence_rank": 1}],
            anchors=[anchor(60_000, 120_000)],
        )
        (gap,) = classify_turn(row)
        self.assertEqual(gap.kind, CITED_ELSEWHERE)

    def test_a_citation_of_another_modality_cannot_stand_in_for_adjacency(self):
        """Distance is only meaningful between an anchor and a modality it
        lists; a page has no place on the clock to be near."""

        row = turn(
            evidence=[
                item(1, None, modality="resource_page", page=33),
                item(2, 90_000),
            ],
            citations=[{"evidence_rank": 1}],
            anchors=[anchor(60_000, 120_000, modalities=("visual_frame",))],
        )
        (gap,) = classify_turn(row)
        self.assertEqual(gap.kind, CITED_ELSEWHERE)
        self.assertIsNone(gap.nearest_citation_ms)

    def test_a_page_anchor_is_judged_on_pages_not_the_clock(self):
        row = turn(
            evidence=[
                item(1, None, modality="resource_page", page=33),
                item(2, None, modality="resource_page", page=80),
            ],
            citations=[{"evidence_rank": 2}],
            anchors=[
                anchor(0, 0, modalities=("resource_page",), pages=[33]),
            ],
        )
        (gap,) = classify_turn(row)
        self.assertEqual(gap.kind, CITED_ELSEWHERE)
        self.assertEqual(gap.reached_ranks, (1,))

    def test_optional_anchors_are_not_scored(self):
        row = turn(
            evidence=[item(1, 1_000)],
            citations=[],
            anchors=[anchor(500, 2_000) | {"role": "optional"}],
        )
        self.assertEqual(classify_turn(row), [])

    def test_a_failed_turn_contributes_no_gaps(self):
        row = {
            "turn_id": "t9",
            "gold": {"expected_evidence": [anchor(0, 1_000)]},
            "prediction": None,
        }
        self.assertEqual(classify_turn(row), [])

    def test_the_breakdown_counts_every_kind_and_names_each_gap(self):
        rows = [
            turn(
                evidence=[item(1, 1_000)],
                citations=[],
                anchors=[anchor(500, 2_000)],
                turn_id="a",
            ),
            turn(
                evidence=[item(1, 130_000), item(2, 90_000)],
                citations=[{"evidence_rank": 1}],
                anchors=[anchor(60_000, 120_000)],
                turn_id="b",
            ),
        ]
        breakdown = classify_citation_gaps(rows)

        self.assertEqual(breakdown["gaps"], 2)
        self.assertEqual(breakdown["turns_affected"], 2)
        self.assertEqual(breakdown["by_kind"][UNCITED], 1)
        self.assertEqual(breakdown["by_kind"][CITED_ADJACENT], 1)
        self.assertEqual(breakdown["by_kind"][CITED_ELSEWHERE], 0)
        self.assertEqual(breakdown["by_route"], {"evidence_qa": 2})
        # Named individually: a total cannot be re-read later to check
        # whether the split still holds.
        self.assertEqual(
            [gap["turn_id"] for gap in breakdown["detail"]], ["a", "b"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
