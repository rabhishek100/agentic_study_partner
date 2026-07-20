from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from parsing.models import ParsedBook, Section, TextBlock
from storage.sqlite import connect, ingest_book, initialize
from study.contracts import (
    ConversationMessage,
    ConversationState,
    EvidenceRef,
    ScopeCandidate,
    ScopeRef,
    TurnDecision,
)
from study.scope_candidates import find_scope_candidates


FILE_HASH = "b" * 64


def hierarchy_book() -> ParsedBook:
    paths = [
        ["Chapter 1. Overview"],
        ["Chapter 3. Data Engineering Fundamentals"],
        ["Chapter 3. Data Engineering Fundamentals", "Modes of Dataflow"],
        [
            "Chapter 3. Data Engineering Fundamentals",
            "Modes of Dataflow",
            "Data Passing Through Services",
        ],
        ["Chapter 4. Training Data"],
        ["Chapter 4. Training Data", "Sampling"],
        ["Chapter 4. Training Data", "Sampling", "Reservoir Sampling"],
        ["Chapter 7. Model Deployment and Prediction Service"],
        ["Chapter 7. Model Deployment and Prediction Service", "Model Compression"],
        [
            "Chapter 7. Model Deployment and Prediction Service",
            "Model Compression",
            "Low-Rank Factorization",
        ],
    ]
    sections = [
        Section(
            path=path,
            level=len(path),
            start_page=page,
            end_page=page,
            texts=[
                TextBlock(
                    text=f"Content for {path[-1]}",
                    category="NarrativeText",
                    page=page,
                )
            ],
        )
        for page, path in enumerate(paths, start=1)
    ]
    return ParsedBook(
        source="sources/books/hierarchy.pdf",
        toc=[
            (section.level, section.title, section.start_page)
            for section in sections
        ],
        sections=sections,
    )


class ScopeCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "books.sqlite3"
        )
        with connect(self.database_path) as connection:
            initialize(connection)
            self.book_id = ingest_book(
                connection,
                hierarchy_book(),
                title="Hierarchy Book",
                author="Test Author",
                file_hash=FILE_HASH,
                page_count=10,
                parser_version="test-v1",
            )
            self.node_ids = {
                row["title"]: row["id"]
                for row in connection.execute(
                    "SELECT id, title FROM nodes"
                )
            }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def state(self, **updates) -> ConversationState:
        values = {
            "conversation_id": "test-conversation",
            "book_id": self.book_id,
        }
        values.update(updates)
        return ConversationState(**values)

    def candidates(
        self,
        question: str,
        state: ConversationState | None = None,
        *,
        limit: int = 8,
    ) -> list[ScopeCandidate]:
        return find_scope_candidates(
            question,
            state or self.state(),
            self.database_path,
            limit=limit,
        )

    def test_explicit_chapter_number_ranks_chapter_first(self):
        candidates = self.candidates("Explain Chapter 3.")

        first = candidates[0]
        self.assertEqual(
            first.node_id,
            self.node_ids["Chapter 3. Data Engineering Fundamentals"],
        )
        self.assertEqual(first.kind, "chapter")
        self.assertEqual((first.start_page, first.end_page), (2, 4))
        self.assertIn("explicit Chapter 3", first.match_reason)

    def test_specific_title_phrase_outranks_broader_title(self):
        candidates = self.candidates(
            "How does reservoir sampling work?"
        )

        self.assertEqual(candidates[0].title, "Reservoir Sampling")
        self.assertIn("Sampling", {item.title for item in candidates})

    def test_partial_title_words_find_section_and_parent_chapter(self):
        candidates = self.candidates("Compare the dataflow modes.")

        self.assertEqual(candidates[0].title, "Modes of Dataflow")
        self.assertIn(
            "Chapter 3. Data Engineering Fundamentals",
            {item.title for item in candidates},
        )

    def test_named_subsection_includes_parent_chapter(self):
        candidates = self.candidates(
            "Could the low-rank factorization section answer it?"
        )

        self.assertEqual(candidates[0].title, "Low-Rank Factorization")
        parent = next(
            item
            for item in candidates
            if item.title
            == "Chapter 7. Model Deployment and Prediction Service"
        )
        self.assertIn("parent chapter", parent.match_reason)

    def test_active_scope_is_available_for_implicit_follow_up(self):
        active = ScopeRef(
            kind="section",
            book_id=self.book_id,
            node_id=self.node_ids["Low-Rank Factorization"],
            display_path=(
                "Chapter 7. Model Deployment and Prediction Service "
                ":: Model Compression :: Low-Rank Factorization"
            ),
            start_page=10,
            end_page=10,
        )

        candidates = self.candidates(
            "What does that section actually cover?",
            self.state(active_scope=active),
        )

        self.assertEqual(candidates[0].node_id, active.node_id)
        self.assertIn("active conversation scope", candidates[0].match_reason)

    def test_previous_evidence_and_recent_chapter_mentions_are_candidates(self):
        services_id = self.node_ids["Data Passing Through Services"]
        state = self.state(
            messages=[
                ConversationMessage(
                    role="user",
                    content="Summarize Chapter 3.",
                    turn_id="t1",
                ),
                ConversationMessage(
                    role="assistant",
                    content="Chapter summary.",
                    turn_id="t1",
                ),
            ],
            previous_evidence=[
                EvidenceRef(
                    node_id=services_id,
                    pages=[4],
                    path=(
                        "Chapter 3. Data Engineering Fundamentals "
                        ":: Modes of Dataflow "
                        ":: Data Passing Through Services"
                    ),
                )
            ],
        )

        candidates = self.candidates("Compare it with the first one.", state)
        by_id = {item.node_id: item for item in candidates}

        self.assertIn(services_id, by_id)
        self.assertIn(
            "previous-turn evidence",
            by_id[services_id].match_reason,
        )
        chapter_id = self.node_ids[
            "Chapter 3. Data Engineering Fundamentals"
        ]
        self.assertIn(chapter_id, by_id)
        self.assertIn("recent Chapter 3", by_id[chapter_id].match_reason)

    def test_unrelated_question_does_not_invent_a_scope(self):
        candidates = self.candidates("What color is the moon?")

        self.assertEqual(candidates, [])

    def test_limit_and_uniqueness_are_enforced(self):
        candidates = self.candidates("Explain sampling.", limit=2)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(
            len({candidate.node_id for candidate in candidates}),
            2,
        )

    def test_nonpositive_limit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "limit must be positive"):
            self.candidates("Chapter 3", limit=0)


class ConversationContractTests(unittest.TestCase):
    def test_valid_retrieval_decision(self):
        decision = TurnDecision(
            route="retrieval_qa",
            history_dependency="dependent",
            standalone_query="What does low-rank factorization cover?",
            reason="The question refers to the prior section.",
        )

        self.assertEqual(decision.route, "retrieval_qa")

    def test_route_specific_requirements_are_enforced(self):
        with self.assertRaisesRegex(
            ValidationError,
            "retrieval QA requires a standalone query",
        ):
            TurnDecision(
                route="retrieval_qa",
                history_dependency="independent",
                reason="Ordinary global question.",
            )

        with self.assertRaisesRegex(
            ValidationError,
            "clarify requires a question",
        ):
            TurnDecision(
                route="clarify",
                history_dependency="ambiguous",
                reason="The ordinal has no known list.",
            )

    def test_candidate_rejects_invalid_pages_and_extra_fields(self):
        values = {
            "book_id": 1,
            "node_id": 2,
            "kind": "section",
            "title": "Example",
            "display_path": "Chapter 1 :: Example",
            "start_page": 3,
            "end_page": 2,
            "match_reason": "title phrase",
        }
        with self.assertRaisesRegex(
            ValidationError,
            "end_page must be at least start_page",
        ):
            ScopeCandidate(**values)
        values["end_page"] = 3
        values["unexpected"] = "rejected"
        with self.assertRaises(ValidationError):
            ScopeCandidate(**values)


if __name__ == "__main__":
    unittest.main()
