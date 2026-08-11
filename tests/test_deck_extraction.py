"""Book-extracted question generation, grounding, storage, and contracts."""

import re
import unittest
from unittest.mock import MagicMock, patch

import fitz
from pydantic import ValidationError

from api.decks import GenerateDeckRequest
from decks import jobs, store
from decks.contracts import CardBack, DeckCard, DeckCitation
from decks.extraction import (
    ExtractedQuestion,
    ExtractedQuestionList,
    RAGAnswerOutput,
    _visual_page_text,
    answer_evidence,
    evidence_batches,
    extract_and_generate_deck,
    numbered_question_candidates,
    question_section_topics,
)
from decks.generate import DeckGenerationError, GeneratedDeck
from decks.topics import ScopeInventory, Topic, book_scope_key
from decks.validate import ValidationTally, build_metrics
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.scope import resolve_chapter
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


def sample_topic(
    key: str = "topic-1",
    *,
    ordinal: int = 0,
    node_id: int = 42,
    evidence_text: str | None = None,
) -> Topic:
    return Topic(
        key=key,
        ordinal=ordinal,
        label=f"Chapter 1 > {key}",
        required=True,
        evidence_text=evidence_text
        or (
            f"[N{node_id}:P1]\nQuestion 1: What is RAG? "
            "Answer: RAG stands for Retrieval-Augmented Generation."
        ),
        allowed_markers=frozenset({f"[N{node_id}:P1]", f"[N{node_id}:P2]"}),
        node_id=node_id,
        start_page=1,
        end_page=2,
    )


def sample_inventory(topic: Topic | None = None) -> ScopeInventory:
    return ScopeInventory(
        source_kind="book",
        scope_key="test-key",
        title="Chapter 1",
        source_title="Sample Book",
        outline="- Chapter 1",
        topics=(topic or sample_topic(),),
    )


class DeckExtractionUnitTests(unittest.TestCase):
    def test_visual_page_text_excludes_outside_margin_callouts(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=700)
        page.insert_text((100, 100), "What is the range using min() and max()?")
        page.insert_text((420, 120), ".min()")
        # This callout shares a baseline with body text. Coordinate-only line
        # grouping must not splice it into the exercise sentence.
        page.insert_text((420, 100), ".max()")

        text = _visual_page_text(page)

        self.assertIn("range using min() and max()?", text)
        self.assertNotIn("\n.min()", text)
        self.assertNotIn("\n.max()", text)

    def test_book_extraction_mode_rejects_video_sources(self) -> None:
        with self.assertRaisesRegex(ValidationError, "require a book chapter"):
            GenerateDeckRequest(
                source_kind="video",
                generation_mode="book_extracted",
                video_id="00000000-0000-4000-8000-000000000001",
            )

    def test_book_scope_key_by_generation_mode(self) -> None:
        self.assertEqual(
            book_scope_key(10, 20, generation_mode="topic_generated"),
            "book:10:node:20",
        )
        self.assertEqual(
            book_scope_key(10, 20, generation_mode="book_extracted"),
            "book:10:node:20:mode:book_extracted",
        )

    def test_extraction_batches_never_send_a_whole_oversized_chapter(self) -> None:
        topic = sample_topic(
            evidence_text="[N42:P1]\n" + "statistical learning " * 8_000
        )
        batches = evidence_batches((topic,), token_budget=500)
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(batch.startswith("[N42:P1]") for batch in batches))
        # A small allowance covers tokenisation around the marker and newline.
        import tiktoken

        from study.context import DEFAULT_ENCODING

        encoding = tiktoken.get_encoding(DEFAULT_ENCODING)
        self.assertTrue(all(len(encoding.encode(batch)) <= 505 for batch in batches))

    def test_answer_evidence_is_bounded_and_keeps_the_question_page(self) -> None:
        topic = sample_topic(
            evidence_text=(
                "[N42:P1]\nWhy does test error estimate prediction error?\n\n"
                "[N42:P2]\nPrediction error estimates generalisation to unseen data."
            )
        )
        evidence, markers = answer_evidence(
            "Why does test error estimate prediction error?",
            "[N42:P1]",
            (topic,),
            token_budget=100,
        )
        self.assertIn("[N42:P1]", evidence)
        self.assertIn("[N42:P1]", markers)

    @patch("decks.extraction._question_extraction_model")
    def test_printed_answer_preserves_source_provenance(self, builder) -> None:
        builder.return_value.invoke.return_value = ExtractedQuestionList(
            questions=[
                ExtractedQuestion(
                    question="What is RAG?",
                    printed_answer="Retrieval-Augmented Generation",
                    citation_marker="[N42:P1]",
                    answer_citation_markers=["[N42:P1]"],
                )
            ]
        )
        generated = extract_and_generate_deck(
            sample_inventory(), connection=MagicMock(), owner_id="owner"
        )
        self.assertEqual(len(generated.cards), 1)
        self.assertEqual(generated.cards[0].front, "What is RAG?")
        self.assertEqual(generated.cards[0].answer_source, "printed_in_book")
        self.assertEqual(generated.metrics.topics_required, 0)
        system_prompt = builder.return_value.invoke.call_args.args[0][0]["content"]
        self.assertIn("never truncate", system_prompt)

    def test_numbered_exercise_inventory_is_lossless_and_excludes_narrative(
        self,
    ) -> None:
        narrative = sample_topic(
            key="narrative",
            evidence_text=(
                "[N40:P20]\nWhich media are associated with sales? "
                "This rhetorical question introduces the chapter."
            ),
        )
        exercises = Topic(
            key="exercises",
            ordinal=1,
            label="Chapter 2 :: 2.4 Exercises :: Applied",
            required=False,
            evidence_text=(
                "[N42:P63]\n2.4 Exercises\nConceptual\n"
                "1. Compare flexible and inflexible methods.\n"
                '(a) Large n and small p.\n(d) Var(") is high.\n'
                "2. Classify each scenario.\n(a) Firms.\n(b) Products.\n(c) Markets.\n"
                "3. Draw all five bias-variance curves.\n(a) Sketch.\n(b) Explain.\n"
                "[N42:P64]\n4. Give applications.\n(a) Classification.\n(b) Regression.\n(c) Clustering.\n"
                "5. Compare flexible approaches.\n"
                "6. Compare parametric approaches.\n"
                "7. Use this training table.\nObs X1 X2 X3 Y\n1 0 3 0 Red\n"
                "(a) Compute distances.\n(b) K=1?\n(c) K=3?\n(d) Choose K.\n"
                "[N42:P65]\n8. Analyze College.csv.\n(a) Read it.\n(b) Set its index.\n"
                + "Preserve this setup sentence. "
                * 90
                + "\n[N42:P66]\n(c) Describe it.\n(d) Plot it.\n(e) Boxplot.\n"
                "(f) Create Elite and count it.\n(g) Histograms.\n(h) Summarize.\n"
                "9. Analyze Auto.\n(a) Types.\n(b) Ranges.\n(c) Means.\n"
                "[N42:P67]\n(d) Subset.\n(e) Plot.\n(f) Predict mpg.\n"
                "10. Analyze Boston.\n(a) Load it.\n(b) Dimensions.\n(c) Plot.\n"
                "(d) Crime associations.\n(e) Ranges.\n(f) Charles river.\n"
                "(g) Median ratio.\n(h) Lowest median home value.\n"
                "(i) Count suburbs above eight rooms per dwelling.\n"
            ),
            allowed_markers=frozenset({f"[N42:P{page}]" for page in range(63, 68)}),
            node_id=42,
            start_page=63,
            end_page=67,
        )

        selected = question_section_topics((narrative, exercises))
        self.assertEqual(selected, (exercises,))
        questions = numbered_question_candidates(selected)
        self.assertEqual(len(questions), 10)
        self.assertEqual(
            [item.source_label for item in questions],
            [f"Exercise {number}" for number in range(1, 11)],
        )
        self.assertNotIn("Which media", "\n".join(item.question for item in questions))
        self.assertIn("Var(ε)", questions[0].question)
        self.assertIn("1 0 3 0 Red", questions[6].question)
        self.assertGreater(len(questions[7].question), 1_000)
        self.assertEqual(
            questions[7].question_citation_markers,
            ["[N42:P65]", "[N42:P66]"],
        )
        self.assertTrue(questions[9].question.endswith("per dwelling."))

        answer_model = MagicMock()

        def complete_answer(messages):
            question = messages[1]["content"].split("\n\nChapter evidence:", 1)[0]
            labels = list(dict.fromkeys(re.findall(r"\([a-z]\)", question)))
            marker = re.search(r"\[E\d+\]", messages[1]["content"]).group(0)
            answer = "\n".join(f"{label} Complete worked answer." for label in labels)
            if not answer:
                answer = "Complete worked answer."
            answer = f"{answer} {marker}"
            return RAGAnswerOutput(
                answer=answer,
                say_it_aloud="Complete worked answer.",
                citation_markers=[marker],
                answer_source="synthesized_from_book",
            )

        answer_model.invoke.side_effect = complete_answer
        inventory = ScopeInventory(
            source_kind="book",
            scope_key="book:1:node:40:mode:book_extracted",
            title="Chapter 2",
            source_title="ISLP",
            outline="- Exercises",
            topics=(narrative, exercises),
        )
        with (
            patch("decks.extraction._question_extraction_model") as extractor,
            patch("decks.extraction._rag_answer_model", return_value=answer_model),
        ):
            generated = extract_and_generate_deck(
                inventory, connection=MagicMock(), owner_id="owner"
            )

        extractor.assert_not_called()
        self.assertEqual(len(generated.cards), 10)
        self.assertEqual(generated.metrics.source_questions_total, 10)
        self.assertEqual(generated.metrics.source_questions_covered, 10)
        self.assertTrue(generated.metrics.complete)
        self.assertNotIn(
            "Which media", "\n".join(card.front for card in generated.cards)
        )
        self.assertIn("per dwelling.", generated.cards[-1].front)
        self.assertNotIn("[E", generated.cards[0].back.answer)
        self.assertIn("[N42:P", generated.cards[0].back.answer)

    @patch("decks.extraction._rag_answer_model")
    @patch("decks.extraction._question_extraction_model")
    def test_incomplete_answer_gets_one_repair_pass(
        self, extractor_builder, answer_builder
    ) -> None:
        extractor_builder.return_value.invoke.return_value = ExtractedQuestionList(
            questions=[
                ExtractedQuestion(
                    question="(a) Define RAG. (b) Explain why it helps.",
                    citation_marker="[N42:P1]",
                )
            ]
        )
        answer_builder.return_value.invoke.side_effect = [
            RAGAnswerOutput(
                answer="The supplied evidence is insufficient.",
                say_it_aloud="Insufficient evidence.",
                citation_markers=["[N42:P1]"],
                answer_source="synthesized_from_book",
            ),
            RAGAnswerOutput(
                answer="(a) RAG retrieves evidence. (b) It grounds generation.",
                say_it_aloud="RAG retrieves before generation.",
                citation_markers=["[N42:P1]"],
                answer_source="synthesized_from_book",
            ),
        ]

        generated = extract_and_generate_deck(
            sample_inventory(), connection=MagicMock(), owner_id="owner"
        )

        self.assertEqual(answer_builder.return_value.invoke.call_count, 2)
        self.assertTrue(generated.metrics.repair_attempted)
        self.assertEqual(generated.metrics.source_questions_covered, 1)

    @patch("decks.extraction._rag_answer_model")
    @patch("decks.extraction._question_extraction_model")
    def test_missing_answer_is_grounded_in_answer_citations(
        self, extractor_builder, answer_builder
    ) -> None:
        extractor_builder.return_value.invoke.return_value = ExtractedQuestionList(
            questions=[
                ExtractedQuestion(
                    question="What is RAG?",
                    citation_marker="[N42:P1]",
                )
            ]
        )
        answer_builder.return_value.invoke.return_value = RAGAnswerOutput(
            answer="RAG augments generation with retrieved evidence.",
            key_points=["It retrieves before generation."],
            say_it_aloud="RAG grounds generation in retrieved evidence.",
            citation_markers=["[N42:P1]"],
            answer_source="synthesized_from_book",
        )
        generated = extract_and_generate_deck(
            sample_inventory(), connection=MagicMock(), owner_id="owner"
        )
        self.assertEqual(generated.cards[0].answer_source, "rag_generated")
        self.assertEqual(
            [citation.marker for citation in generated.cards[0].citations],
            ["[N42:P1]"],
        )

    @patch("decks.extraction._rag_answer_model")
    @patch("decks.extraction._question_extraction_model")
    def test_printed_solution_found_elsewhere_keeps_printed_provenance(
        self, extractor_builder, answer_builder
    ) -> None:
        topic = sample_topic(
            evidence_text=(
                "[N42:P1]\nReview question: What is RAG?\n\n"
                "[N42:P2]\nSolution: RAG is Retrieval-Augmented Generation."
            )
        )
        extractor_builder.return_value.invoke.return_value = ExtractedQuestionList(
            questions=[
                ExtractedQuestion(
                    question="What is RAG?",
                    citation_marker="[N42:P1]",
                )
            ]
        )
        answer_builder.return_value.invoke.return_value = RAGAnswerOutput(
            answer="RAG is Retrieval-Augmented Generation.",
            say_it_aloud="RAG is Retrieval-Augmented Generation.",
            citation_markers=["[N42:P2]"],
            answer_source="printed_in_book",
        )
        generated = extract_and_generate_deck(
            sample_inventory(topic), connection=MagicMock(), owner_id="owner"
        )
        self.assertEqual(generated.cards[0].answer_source, "printed_in_book")
        self.assertEqual(
            [citation.marker for citation in generated.cards[0].citations],
            ["[N42:P1]", "[N42:P2]"],
        )

    @patch("decks.extraction._question_extraction_model")
    def test_no_questions_is_a_successful_empty_source_deck(self, builder) -> None:
        builder.return_value.invoke.return_value = ExtractedQuestionList(questions=[])
        generated = extract_and_generate_deck(
            sample_inventory(), connection=MagicMock(), owner_id="owner"
        )
        self.assertEqual(generated.cards, ())
        self.assertEqual(
            generated.metrics.notice, "No questions printed in this chapter"
        )
        self.assertTrue(generated.metrics.complete)

    @patch("decks.extraction._question_extraction_model")
    def test_provider_failure_is_retryable_instead_of_no_questions(
        self, builder
    ) -> None:
        builder.return_value.invoke.side_effect = TimeoutError("provider timeout")
        with self.assertRaisesRegex(DeckGenerationError, "batch 1"):
            extract_and_generate_deck(
                sample_inventory(), connection=MagicMock(), owner_id="owner"
            )

    @patch("decks.extraction._question_extraction_model")
    def test_incomplete_question_inventory_fails_instead_of_publishing(
        self, builder
    ) -> None:
        builder.return_value.invoke.return_value = ExtractedQuestionList(
            questions=[
                ExtractedQuestion(
                    question="What is RAG?",
                    printed_answer="Retrieval-Augmented Generation",
                    citation_marker="[N42:P1]",
                ),
                ExtractedQuestion(
                    question="What is RAG?",
                    printed_answer="Retrieval-Augmented Generation",
                    citation_marker="[N42:P1]",
                ),
            ]
        )
        with self.assertRaisesRegex(DeckGenerationError, "incomplete"):
            extract_and_generate_deck(
                sample_inventory(), connection=MagicMock(), owner_id="owner"
            )

    @patch("decks.extraction._question_extraction_model")
    def test_invented_question_marker_is_not_replaced_with_first_page(
        self, builder
    ) -> None:
        builder.return_value.invoke.return_value = ExtractedQuestionList(
            questions=[
                ExtractedQuestion(
                    question="What is RAG?",
                    printed_answer="Retrieval-Augmented Generation",
                    citation_marker="[N999:P99]",
                )
            ]
        )
        with self.assertRaisesRegex(
            DeckGenerationError, "outside its supplied evidence"
        ):
            extract_and_generate_deck(
                sample_inventory(), connection=MagicMock(), owner_id="owner"
            )


class DeckExtractionStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Book",
            author="Test Author",
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        self.scope = resolve_chapter(
            self.connection,
            "Chapter 1",
            owner_id=self.owner_id,
            book_id=self.book_id,
        )

    def tearDown(self) -> None:
        if hasattr(self, "database_context"):
            self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def test_enqueue_and_store_book_extracted_deck(self) -> None:
        scope_key = book_scope_key(
            self.book_id,
            self.scope.root_node_id,
            generation_mode="book_extracted",
        )
        job = jobs.enqueue(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=scope_key,
            generation_mode="book_extracted",
            book_id=self.book_id,
            node_id=self.scope.root_node_id,
        )
        self.assertEqual(job.generation_mode, "book_extracted")

        deck_id, _ = store.create_deck(
            self.connection,
            owner_id=self.owner_id,
            source_kind="book",
            scope_key=scope_key,
            title="Chapter 1 Extracted Questions",
            source_title="Sample Book",
            generation_mode="book_extracted",
            book_id=self.book_id,
            node_id=self.scope.root_node_id,
        )
        topic = sample_topic(node_id=self.scope.root_node_id)
        card = DeckCard(
            topic_key=topic.key,
            card_index=0,
            card_type="qa",
            front="What is RAG?",
            back=CardBack(
                answer="Retrieval-Augmented Generation",
                say_it_aloud="RAG is Retrieval-Augmented Generation",
            ),
            citations=[
                DeckCitation(
                    marker=f"[N{self.scope.root_node_id}:P1]",
                    node_id=self.scope.root_node_id,
                    page=1,
                )
            ],
            interview_priority=3,
            answer_source="printed_in_book",
        )
        inventory = ScopeInventory(
            source_kind="book",
            scope_key=scope_key,
            title="Chapter 1",
            source_title="Sample Book",
            outline="- Chapter 1",
            topics=(topic,),
        )
        tally = ValidationTally(generated=1)
        tally.keep(card)
        generated = GeneratedDeck(
            inventory=inventory,
            cards=(card,),
            metrics=build_metrics(
                tally,
                topics=(topic,),
                covered_keys={topic.key},
                repair_attempted=False,
            ),
            model_name="test-model",
            prompt_version="v2_book_extracted",
        )
        store.store_deck(
            self.connection,
            owner_id=self.owner_id,
            deck_id=deck_id,
            topics=(topic,),
            generated=generated,
        )

        summary = store.get_deck(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        self.assertEqual(summary.generation_mode, "book_extracted")
        cards = store.deck_cards(
            self.connection, owner_id=self.owner_id, deck_id=deck_id
        )
        self.assertEqual(cards[0].card.answer_source, "printed_in_book")


if __name__ == "__main__":
    unittest.main()
