"""Generation, validation, and coverage for flashcard decks — no database."""

import unittest

from decks.contracts import CardBack, GeneratedCard, McqOption, TopicCards
from decks.generate import GenerationConfig, attribute_topic, generate_deck
from decks.topics import ScopeInventory, Topic, generation_batches
from decks.validate import (
    DROP_DUPLICATE,
    DROP_MALFORMED,
    DROP_OUT_OF_SCOPE,
    DROP_UNCITED,
    ValidationTally,
    back_is_well_formed,
    normalized_front,
    parse_marker,
    validate_card,
)


def topic(
    ordinal: int,
    *,
    node_id: int = 10,
    pages: tuple[int, ...] = (5,),
    required: bool = True,
    evidence: str = "x" * 600,
) -> Topic:
    markers = frozenset(f"[N{node_id}:P{page}]" for page in pages)
    return Topic(
        key=f"node:{node_id}",
        ordinal=ordinal,
        label=f"Chapter 1 > Section {ordinal + 1}",
        required=required,
        evidence_text=evidence,
        allowed_markers=markers,
        node_id=node_id,
        start_page=pages[0],
        end_page=pages[-1],
    )


def qa_card(
    *,
    topic_ordinal: int = 1,
    front: str = "What is backpressure?",
    markers: list[str] | None = None,
) -> GeneratedCard:
    cited = markers if markers is not None else ["[N10:P5]"]
    inline = f" {cited[0]}" if cited else ""
    return GeneratedCard(
        topic_ordinal=topic_ordinal,
        card_type="qa",
        front=front,
        back=CardBack(
            answer=f"A flow-control signal from a slow consumer.{inline}",
            key_points=["Prevents unbounded queues"],
            say_it_aloud="Slow consumers push back on fast producers.",
        ),
        citation_markers=cited,
        interview_priority=5,
        priority_reason="Central to streaming design questions.",
    )


class MarkerTests(unittest.TestCase):
    def test_parses_a_book_marker(self) -> None:
        citation = parse_marker("[N42:P7]")
        assert citation is not None
        self.assertEqual((citation.node_id, citation.page), (42, 7))
        self.assertIsNone(citation.evidence_rank)

    def test_parses_a_lecture_marker(self) -> None:
        citation = parse_marker("[S3]")
        assert citation is not None
        self.assertEqual(citation.evidence_rank, 3)
        self.assertIsNone(citation.node_id)

    def test_rejects_a_shape_that_is_not_a_marker(self) -> None:
        self.assertIsNone(parse_marker("[see chapter 4]"))

    def test_normalized_front_ignores_markers_and_punctuation(self) -> None:
        self.assertEqual(
            normalized_front("What is *backpressure*? [N10:P5]"),
            normalized_front("what is backpressure"),
        )


class BackShapeTests(unittest.TestCase):
    def test_mcq_needs_four_options_and_exactly_one_correct(self) -> None:
        options = [
            McqOption(label=label, text=f"option {label}", correct=label == "A")
            for label in ("A", "B", "C", "D")
        ]
        self.assertTrue(back_is_well_formed("mcq", CardBack(options=options)))

        two_correct = [
            option.model_copy(update={"correct": option.label in ("A", "B")})
            for option in options
        ]
        self.assertFalse(back_is_well_formed("mcq", CardBack(options=two_correct)))
        self.assertFalse(back_is_well_formed("mcq", CardBack(options=options[:3])))

    def test_system_design_needs_structure_not_only_prose(self) -> None:
        prose_only = CardBack(answer="It has a queue and some workers.")
        self.assertFalse(back_is_well_formed("system_design", prose_only))

        structured = CardBack(
            answer="A queue decouples ingest from training.",
            components=["Ingest API", "Queue", "Trainer"],
            data_flow=["Client posts an event", "Queue buffers it"],
        )
        self.assertTrue(back_is_well_formed("system_design", structured))

    def test_qa_needs_an_answer(self) -> None:
        self.assertFalse(back_is_well_formed("qa", CardBack(say_it_aloud="just this")))


class ValidateCardTests(unittest.TestCase):
    def test_keeps_a_grounded_card_and_resolves_its_citations(self) -> None:
        card, reason = validate_card(
            qa_card(), topic(0), card_index=0, seen_fronts=set()
        )
        self.assertIsNone(reason)
        assert card is not None
        self.assertEqual([citation.marker for citation in card.citations], ["[N10:P5]"])
        self.assertEqual(card.interview_priority, 5)

    def test_drops_a_card_citing_outside_its_topic(self) -> None:
        card, reason = validate_card(
            qa_card(markers=["[N99:P1]"]),
            topic(0),
            card_index=0,
            seen_fronts=set(),
        )
        self.assertIsNone(card)
        self.assertEqual(reason, DROP_OUT_OF_SCOPE)

    def test_drops_a_card_whose_prose_quotes_a_foreign_marker(self) -> None:
        """Declared citations can be right while the answer sends you elsewhere."""

        sneaky = qa_card()
        sneaky = sneaky.model_copy(
            update={
                "back": sneaky.back.model_copy(
                    update={"answer": "As shown in [N77:P3], it buffers."}
                )
            }
        )
        card, reason = validate_card(
            sneaky, topic(0), card_index=0, seen_fronts=set()
        )
        self.assertIsNone(card)
        self.assertEqual(reason, DROP_OUT_OF_SCOPE)

    def test_drops_an_uncited_card(self) -> None:
        bare = qa_card(markers=[])
        bare = bare.model_copy(
            update={"back": bare.back.model_copy(update={"answer": "No markers here."})}
        )
        card, reason = validate_card(bare, topic(0), card_index=0, seen_fronts=set())
        self.assertIsNone(card)
        self.assertEqual(reason, DROP_UNCITED)

    def test_drops_a_repeated_front(self) -> None:
        seen: set[str] = set()
        validate_card(qa_card(), topic(0), card_index=0, seen_fronts=seen)
        card, reason = validate_card(
            qa_card(front="What is BACKPRESSURE?!"),
            topic(0),
            card_index=1,
            seen_fronts=seen,
        )
        self.assertIsNone(card)
        self.assertEqual(reason, DROP_DUPLICATE)

    def test_drops_a_malformed_mcq(self) -> None:
        broken = GeneratedCard(
            topic_ordinal=1,
            card_type="mcq",
            front="Which is true?",
            back=CardBack(
                options=[
                    McqOption(label="A", text="one", correct=True),
                    McqOption(label="B", text="two", correct=True),
                    McqOption(label="C", text="three", correct=False),
                    McqOption(label="D", text="four", correct=False),
                ]
            ),
            citation_markers=["[N10:P5]"],
        )
        card, reason = validate_card(broken, topic(0), card_index=0, seen_fronts=set())
        self.assertIsNone(card)
        self.assertEqual(reason, DROP_MALFORMED)


class AttributionTests(unittest.TestCase):
    def test_markers_override_a_wrong_declared_ordinal(self) -> None:
        first, second = topic(0, node_id=10), topic(1, node_id=20, pages=(9,))
        card = qa_card(topic_ordinal=1, markers=["[N20:P9]"])
        self.assertIs(attribute_topic(card, (first, second)), second)

    def test_a_card_straddling_two_topics_is_attributed_nowhere(self) -> None:
        first, second = topic(0, node_id=10), topic(1, node_id=20, pages=(9,))
        card = qa_card(topic_ordinal=1, markers=["[N10:P5]", "[N20:P9]"])
        self.assertIsNone(attribute_topic(card, (first, second)))


class BatchingTests(unittest.TestCase):
    def test_batches_respect_the_token_budget_without_merging_topics(self) -> None:
        topics = tuple(
            topic(index, node_id=10 + index, evidence="word " * 400)
            for index in range(4)
        )
        batches = generation_batches(topics, token_budget=500)
        self.assertGreater(len(batches), 1)
        flattened = [item.key for batch in batches for item in batch]
        self.assertEqual(flattened, [item.key for item in topics])

    def test_one_oversized_topic_still_gets_its_own_call(self) -> None:
        topics = (topic(0, evidence="word " * 5_000),)
        self.assertEqual(len(generation_batches(topics, token_budget=100)), 1)


class ScriptedModel:
    """Returns one prepared `TopicCards` per call, then empties."""

    def __init__(self, *responses: TopicCards) -> None:
        self.responses = list(responses)
        self.calls: list[list[tuple[str, str]]] = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return TopicCards(cards=[])


class FailingModel:
    def invoke(self, messages):
        raise RuntimeError("provider unavailable")


def inventory(*topics: Topic) -> ScopeInventory:
    return ScopeInventory(
        source_kind="book",
        scope_key="book:1:node:10",
        title="Chapter 1",
        source_title="Designing ML Systems",
        outline="- Chapter 1",
        topics=topics,
    )


class GenerateDeckTests(unittest.TestCase):
    def test_reports_full_coverage_when_every_topic_gets_a_card(self) -> None:
        first, second = topic(0, node_id=10), topic(1, node_id=20, pages=(9,))
        model = ScriptedModel(
            TopicCards(
                cards=[
                    qa_card(topic_ordinal=1, markers=["[N10:P5]"]),
                    qa_card(
                        topic_ordinal=2,
                        front="What is a feature store?",
                        markers=["[N20:P9]"],
                    ),
                ]
            )
        )
        deck = generate_deck(inventory(first, second), model=model)
        self.assertTrue(deck.complete)
        self.assertEqual(deck.metrics.topics_covered, 2)
        self.assertEqual(deck.metrics.cards_kept, 2)
        self.assertEqual([card.card_index for card in deck.cards], [0, 1])

    def test_repairs_a_topic_the_first_pass_missed(self) -> None:
        first, second = topic(0, node_id=10), topic(1, node_id=20, pages=(9,))
        model = ScriptedModel(
            TopicCards(cards=[qa_card(topic_ordinal=1, markers=["[N10:P5]"])]),
            TopicCards(
                cards=[
                    qa_card(
                        topic_ordinal=2,
                        front="What is a feature store?",
                        markers=["[N20:P9]"],
                    )
                ]
            ),
        )
        deck = generate_deck(inventory(first, second), model=model)
        self.assertTrue(deck.metrics.repair_attempted)
        self.assertTrue(deck.complete)
        self.assertIn("earlier pass produced no usable card", model.calls[1][1][1])

    def test_an_uncovered_topic_is_named_rather_than_hidden(self) -> None:
        first, second = topic(0, node_id=10), topic(1, node_id=20, pages=(9,))
        model = ScriptedModel(
            TopicCards(cards=[qa_card(topic_ordinal=1, markers=["[N10:P5]"])])
        )
        deck = generate_deck(inventory(first, second), model=model)
        self.assertFalse(deck.complete)
        self.assertEqual(deck.metrics.topics_covered, 1)
        self.assertEqual(deck.metrics.uncovered_topic_labels, [second.label])

    def test_an_optional_topic_never_fails_coverage(self) -> None:
        first = topic(0, node_id=10)
        thin = topic(1, node_id=20, pages=(9,), required=False, evidence="short")
        model = ScriptedModel(
            TopicCards(cards=[qa_card(topic_ordinal=1, markers=["[N10:P5]"])])
        )
        deck = generate_deck(inventory(first, thin), model=model)
        self.assertTrue(deck.complete)
        self.assertEqual(deck.metrics.topics_total, 2)
        self.assertEqual(deck.metrics.topics_required, 1)

    def test_a_provider_failure_costs_its_batch_not_the_deck(self) -> None:
        deck = generate_deck(
            inventory(topic(0)),
            model=FailingModel(),
            config=GenerationConfig(repair=False),
        )
        self.assertEqual(deck.metrics.cards_kept, 0)
        self.assertEqual(deck.metrics.uncovered_topic_labels, [topic(0).label])

    def test_cards_are_ordered_by_source_then_priority(self) -> None:
        only = topic(0)
        model = ScriptedModel(
            TopicCards(
                cards=[
                    qa_card(front="Peripheral detail?", markers=["[N10:P5]"]).model_copy(
                        update={"interview_priority": 2}
                    ),
                    qa_card(front="The central idea?", markers=["[N10:P5]"]).model_copy(
                        update={"interview_priority": 5}
                    ),
                ]
            )
        )
        deck = generate_deck(inventory(only), model=model, config=GenerationConfig(repair=False))
        self.assertEqual(
            [card.front for card in deck.cards],
            ["The central idea?", "Peripheral detail?"],
        )

    def test_progress_reports_every_topic(self) -> None:
        seen: list[tuple[int, int]] = []
        generate_deck(
            inventory(topic(0), topic(1, node_id=20, pages=(9,))),
            model=ScriptedModel(),
            config=GenerationConfig(repair=False),
            progress=lambda done, total: seen.append((done, total)),
        )
        self.assertEqual(seen[-1], (2, 2))


class TallyTests(unittest.TestCase):
    def test_counts_are_kept_per_reason(self) -> None:
        tally = ValidationTally()
        tally.drop(DROP_DUPLICATE)
        tally.drop(DROP_DUPLICATE)
        tally.drop(DROP_UNCITED)
        self.assertEqual(tally.dropped, {DROP_DUPLICATE: 2, DROP_UNCITED: 1})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
