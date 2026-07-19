from types import SimpleNamespace
import unittest

from scripts.study import format_dry_run, format_outline
from storage.sqlite import connect, ingest_book, initialize
from study.content import load_scope_content
from study.context import build_scope_context
from study.request import (
    StudyRequest,
    UnsupportedStudyRequestError,
    parse_study_request,
    resolve_study_request,
)
from study.scope import resolve_chapter
from study.summarize import (
    ContextWindowExceededError,
    SummaryConfig,
    summarize_scope,
    validate_summary,
)
from tests.test_storage import FILE_HASH, sample_book


class FakeSummaryModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.response)


class StudySummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        initialize(self.connection)
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            title="Sample Book",
            author="Test Author",
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )

    def tearDown(self) -> None:
        self.connection.close()

    def _chapter_context(self):
        scope = resolve_chapter(
            self.connection,
            1,
            book_id=self.book_id,
        )
        evidence = load_scope_content(self.connection, scope)
        return scope, build_scope_context(evidence)

    def test_parses_supported_natural_language_requests(self) -> None:
        self.assertEqual(
            parse_study_request("Summarize Chapter 1"),
            StudyRequest("summarize", "chapter", "1"),
        )
        self.assertEqual(
            parse_study_request(
                "Summarize section Core idea in chapter 1"
            ),
            StudyRequest("summarize", "section", "Core idea", "1"),
        )
        self.assertEqual(
            parse_study_request(
                "What sections are present in Chapter 1?"
            ),
            StudyRequest("list_sections", "chapter", "1"),
        )
        self.assertEqual(
            parse_study_request("Summarize Core idea"),
            StudyRequest("summarize", "named", "Core idea"),
        )
        with self.assertRaises(UnsupportedStudyRequestError):
            parse_study_request("Tell me something interesting")

    def test_maps_named_request_to_the_exact_section_subtree(self) -> None:
        request = parse_study_request("Summarize Core idea")
        scope = resolve_study_request(
            self.connection,
            request,
            book_id=self.book_id,
        )

        self.assertEqual(scope.kind, "section")
        self.assertEqual(
            [node.title for node in scope.nodes],
            ["Core idea", "Diagram"],
        )

    def test_context_contains_all_blocks_without_chunk_overlap(self) -> None:
        scope, context = self._chapter_context()

        self.assertEqual(context.included_block_count, 5)
        self.assertEqual(context.skipped_block_count, 0)
        self.assertEqual(context.table_count, 1)
        self.assertEqual(context.image_count, 1)
        self.assertEqual(context.expected_node_ids, frozenset(scope.node_ids))
        self.assertIn("Table:\nA", context.text)
        self.assertIn("image payload omitted", context.text)
        self.assertEqual(context.text.count("Before table"), 1)

    def test_context_skips_known_layout_noise(self) -> None:
        first_block_id = self.connection.execute(
            "SELECT MIN(id) FROM content_blocks"
        ).fetchone()[0]
        self.connection.execute(
            "UPDATE content_blocks SET category = 'Header' WHERE id = ?",
            (first_block_id,),
        )
        scope, context = self._chapter_context()

        self.assertEqual(context.included_block_count, 4)
        self.assertEqual(context.skipped_block_count, 1)
        self.assertNotIn(scope.root_node_id, context.expected_node_ids)
        self.assertNotIn("Chapter introduction", context.text)

    def test_one_call_summary_passes_complete_context_and_validates(self) -> None:
        scope, context = self._chapter_context()
        node_pages = {
            node.id: next(
                page
                for candidate_node, page in context.allowed_citations
                if candidate_node == node.id
            )
            for node in scope.nodes
        }
        citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id, page in node_pages.items()
        )
        model = FakeSummaryModel(
            f"# Chapter 1\n\n## Overview\n\nComplete summary. {citations}"
        )

        result = summarize_scope(
            model,
            scope=scope,
            context=context,
        )

        self.assertTrue(result.validation.valid)
        self.assertIsNotNone(model.messages)
        self.assertIn(context.text, model.messages[1][1])

    def test_validation_rejects_invented_and_missing_citations(self) -> None:
        scope, context = self._chapter_context()
        validation = validate_summary(
            "Unsupported claim. [N999:P999]",
            scope=scope,
            context=context,
        )

        self.assertFalse(validation.valid)
        self.assertTrue(validation.missing_node_ids)
        self.assertTrue(
            any("out-of-scope citations" in error for error in validation.errors)
        )

    def test_context_limit_fails_without_calling_model(self) -> None:
        scope, context = self._chapter_context()
        model = FakeSummaryModel("should not be used")

        with self.assertRaises(ContextWindowExceededError):
            summarize_scope(
                model,
                scope=scope,
                context=context,
                config=SummaryConfig(
                    context_window_tokens=100,
                    max_output_tokens=50,
                    safety_margin_tokens=10,
                ),
            )
        self.assertIsNone(model.messages)

    def test_outline_and_dry_run_are_inspectable(self) -> None:
        scope, context = self._chapter_context()
        outline = format_outline(scope)
        dry_run = format_dry_run(
            StudyRequest("summarize", "chapter", "1"),
            scope,
            context,
            config=SummaryConfig(),
        )

        self.assertIn("- Core idea", outline)
        self.assertIn("  - Diagram", outline)
        self.assertIn("Fits without truncation: yes", dry_run)
        self.assertIn("Included blocks: 5", dry_run)


if __name__ == "__main__":
    unittest.main()
