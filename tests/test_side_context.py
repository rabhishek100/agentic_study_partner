"""Side chat context assembly: what gets priority, and what gets dropped."""

import unittest

from study.contracts import CitationRef, EvidenceRef, QuoteAnchor
from study.side_context import (
    ANCHORED_TURN_HEADING,
    EARLIER_HEADING,
    QUOTE_HEADING,
    ParentTurn,
    build_side_context,
    readable_quote,
    resolve_pins,
)


def evidence(rank: int, chunk_id: str) -> EvidenceRef:
    return EvidenceRef(
        node_id=rank * 10,
        pages=[rank],
        path=f"Chapter {rank}",
        chunk_id=chunk_id,
        rank=rank,
    )


def parent_turn(
    turn_index: int = 0,
    question: str = "What is training-serving skew?",
    answer: str = "Skew arises when features differ [S1], and it compounds [S2].",
    citation_ranks: tuple[int, ...] = (1,),
) -> ParentTurn:
    return ParentTurn(
        turn_index=turn_index,
        question=question,
        answer=answer,
        evidence=(evidence(1, "chunk-one"), evidence(2, "chunk-two")),
        citations=tuple(
            CitationRef(
                marker=f"[S{rank}]",
                node_id=rank * 10,
                page=rank,
                evidence_rank=rank,
            )
            for rank in citation_ranks
        ),
    )


def anchor(quoted_text: str, turn_index: int = 0, anchor_id: str = "a1") -> QuoteAnchor:
    return QuoteAnchor(
        anchor_id=anchor_id,
        parent_turn_index=turn_index,
        quoted_text=quoted_text,
    )


class ResolvePinsTests(unittest.TestCase):
    def test_markers_in_the_quote_name_the_chunks_to_pin(self):
        pins = resolve_pins(anchor("it compounds [S2]"), parent_turn())

        self.assertEqual(pins, ("chunk-two",))

    def test_pins_follow_the_order_the_quote_names_them(self):
        pins = resolve_pins(anchor("compounds [S2] because [S1]"), parent_turn())

        self.assertEqual(pins, ("chunk-two", "chunk-one"))

    def test_a_repeated_marker_pins_its_chunk_once(self):
        pins = resolve_pins(anchor("[S1] and again [S1]"), parent_turn())

        self.assertEqual(pins, ("chunk-one",))

    def test_a_marker_the_turn_cannot_account_for_is_skipped_not_guessed(self):
        pins = resolve_pins(anchor("as shown [S9]"), parent_turn())

        # Falls back to what the answer cited rather than inventing a chunk for
        # a rank that turn never had.
        self.assertEqual(pins, ("chunk-one",))

    def test_an_uncited_selection_falls_back_to_what_the_answer_cited(self):
        pins = resolve_pins(
            anchor("features differ between training and serving"),
            parent_turn(citation_ranks=(2,)),
        )

        self.assertEqual(pins, ("chunk-two",))

    def test_a_turn_that_cited_nothing_falls_back_to_its_best_evidence(self):
        pins = resolve_pins(
            anchor("features differ"),
            parent_turn(citation_ranks=()),
        )

        self.assertEqual(pins, ("chunk-one",))

    def test_a_turn_with_no_evidence_pins_nothing(self):
        turn = ParentTurn(turn_index=0, question="Q", answer="A")

        self.assertEqual(resolve_pins(anchor("A"), turn), ())

    def test_list_position_stands_in_for_a_missing_rank(self):
        turn = ParentTurn(
            turn_index=0,
            question="Q",
            answer="A [S2]",
            evidence=(
                EvidenceRef(node_id=1, pages=[1], path="one", chunk_id="chunk-one"),
                EvidenceRef(node_id=2, pages=[2], path="two", chunk_id="chunk-two"),
            ),
        )

        self.assertEqual(resolve_pins(anchor("A [S2]"), turn), ("chunk-two",))


class ReadableQuoteTests(unittest.TestCase):
    def test_citation_markers_are_removed_for_naming(self):
        self.assertEqual(
            readable_quote("Features differ [S1] and compound [S2]."),
            "Features differ and compound .",
        )

    def test_a_selection_of_only_markers_leaves_nothing_to_name(self):
        self.assertEqual(readable_quote("[S2] [S4]"), "")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(readable_quote("  a   b\nc "), "a b c")


class BuildSideContextTests(unittest.TestCase):
    def test_the_quoted_passage_leads_and_is_marked_as_not_evidence(self):
        context = build_side_context([anchor("features differ [S1]")], [parent_turn()])

        self.assertTrue(context.request_context.startswith(QUOTE_HEADING))
        self.assertIn("not source evidence", context.request_context)
        self.assertIn('"features differ [S1]"', context.request_context)

    def test_the_anchored_exchange_follows_the_quote(self):
        context = build_side_context([anchor("features differ [S1]")], [parent_turn()])

        self.assertLess(
            context.request_context.index(QUOTE_HEADING),
            context.request_context.index(ANCHORED_TURN_HEADING),
        )

    def test_earlier_turns_are_compressed_and_come_last(self):
        turns = [
            parent_turn(0, question="Earlier question", answer="Earlier answer [S1]"),
            parent_turn(1),
        ]
        context = build_side_context([anchor("differ [S1]", turn_index=1)], turns)

        self.assertIn("Earlier question", context.request_context)
        self.assertLess(
            context.request_context.index(ANCHORED_TURN_HEADING),
            context.request_context.index(EARLIER_HEADING),
        )

    def test_the_anchored_turn_is_not_repeated_as_an_earlier_turn(self):
        context = build_side_context([anchor("differ [S1]")], [parent_turn()])

        self.assertNotIn(EARLIER_HEADING, context.request_context)

    def test_quotes_are_handed_to_the_analyser_separately(self):
        context = build_side_context(
            [anchor("features differ [S1]"), anchor("it compounds", anchor_id="a2")],
            [parent_turn()],
        )

        self.assertEqual(
            context.anchored_quotes,
            ("features differ [S1]", "it compounds"),
        )

    def test_several_anchors_pin_the_union_of_their_chunks_in_order(self):
        context = build_side_context(
            [anchor("compounds [S2]"), anchor("differ [S1]", anchor_id="a2")],
            [parent_turn()],
        )

        self.assertEqual(context.pinned_chunk_ids, ("chunk-two", "chunk-one"))
        self.assertEqual(context.report.anchor_ids, ["a1", "a2"])

    def test_pins_beyond_the_limit_are_dropped_and_reported(self):
        turn = ParentTurn(
            turn_index=0,
            question="Q",
            answer="A",
            evidence=tuple(evidence(rank, f"chunk-{rank}") for rank in range(1, 5)),
        )
        context = build_side_context(
            [anchor("[S1] [S2] [S3] [S4]")],
            [turn],
            max_pinned_chunks=2,
        )

        self.assertEqual(context.pinned_chunk_ids, ("chunk-1", "chunk-2"))
        self.assertTrue(
            any("beyond the limit" in entry for entry in context.report.dropped)
        )

    def test_surrounding_context_is_dropped_before_the_quote_is(self):
        turns = [parent_turn(index) for index in range(4)]
        context = build_side_context(
            [anchor("features differ [S1]", turn_index=3)],
            turns,
            # Enough for the quoted passage and nothing else.
            token_budget=60,
        )

        self.assertIn('"features differ [S1]"', context.request_context)
        self.assertNotIn(ANCHORED_TURN_HEADING, context.request_context)
        self.assertNotIn(EARLIER_HEADING, context.request_context)
        self.assertEqual(len(context.report.dropped), 2)

    def test_a_quote_larger_than_the_budget_is_truncated_and_reported(self):
        context = build_side_context(
            [anchor("skew " * 400)],
            [parent_turn()],
            token_budget=50,
        )

        self.assertLessEqual(context.report.token_count, 50)
        self.assertIn(
            "quoted passages truncated to fit the budget",
            context.report.dropped,
        )

    def test_the_report_states_the_budget_it_was_measured_against(self):
        context = build_side_context(
            [anchor("features differ [S1]")],
            [parent_turn()],
            token_budget=900,
        )

        self.assertEqual(context.report.token_budget, 900)
        self.assertGreater(context.report.token_count, 0)
        self.assertLessEqual(context.report.token_count, 900)

    def test_an_anchor_whose_turn_is_gone_is_reported_rather_than_ignored(self):
        context = build_side_context(
            [anchor("features differ [S1]", turn_index=7)],
            [parent_turn(0)],
        )

        self.assertEqual(context.pinned_chunk_ids, ())
        self.assertIn(
            "anchor a1: parent turn is missing",
            context.report.dropped,
        )

    def test_a_side_chat_with_no_anchors_still_carries_the_conversation(self):
        context = build_side_context([], [parent_turn(0), parent_turn(1)])

        self.assertNotIn(QUOTE_HEADING, context.request_context)
        self.assertIn(EARLIER_HEADING, context.request_context)
        self.assertEqual(context.pinned_chunk_ids, ())

    def test_no_parent_turns_yields_empty_context_rather_than_failing(self):
        context = build_side_context([], [])

        self.assertEqual(context.request_context, "")
        self.assertEqual(context.report.token_count, 0)

    def test_a_negative_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            build_side_context([], [], token_budget=-1)


if __name__ == "__main__":
    unittest.main()
