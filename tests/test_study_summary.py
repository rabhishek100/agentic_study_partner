import unittest
from dataclasses import replace
from types import SimpleNamespace

from scripts.study import format_dry_run, format_outline
from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.content import load_scope_content
from study.context import build_scope_context
from study.request import (
    StudyRequest,
    UnsupportedStudyRequestError,
    parse_study_request,
    resolve_study_request,
)
from study.scope import ScopeNode, resolve_chapter
from study.summarize import (
    ContextWindowExceededError,
    SummaryConfig,
    append_references,
    normalize_citation_syntax,
    summarize_scope,
    summarize_scope_with_repair,
    validate_summary,
)
from tests.fixtures import FILE_HASH, sample_book
from tests.postgres import PostgresOwnerMixin


class FakeSummaryModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.response)


class SequenceSummaryModel:
    def __init__(self, *responses: str) -> None:
        self.responses = responses
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(
            content=self.responses[len(self.messages) - 1],
            response_metadata={"finish_reason": "stop"},
        )


class StudySummaryTests(PostgresOwnerMixin, unittest.TestCase):
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

    def tearDown(self) -> None:
        self.database_context.__exit__(None, None, None)
        self.tearDownPostgresOwner()

    def _chapter_context(self):
        scope = resolve_chapter(
            self.connection,
            1,
            owner_id=self.owner_id,
            book_id=self.book_id,
        )
        evidence = load_scope_content(
            self.connection,
            scope,
            owner_id=self.owner_id,
        )
        return scope, build_scope_context(evidence)

    def test_parses_supported_natural_language_requests(self) -> None:
        self.assertEqual(
            parse_study_request("Summarize Chapter 1"),
            StudyRequest("summarize", "chapter", "1"),
        )
        self.assertEqual(
            parse_study_request("Summarize section Core idea in chapter 1"),
            StudyRequest("summarize", "section", "Core idea", "1"),
        )
        self.assertEqual(
            parse_study_request("What sections are present in Chapter 1?"),
            StudyRequest("list_sections", "chapter", "1"),
        )
        for question in (
            "What are the sections under Fine-tuning?",
            "Which sections are in Fine-tuning?",
            "List sections of Fine-tuning",
            "Show me all the sections under Fine-tuning.",
        ):
            with self.subTest(question=question):
                self.assertEqual(
                    parse_study_request(question),
                    StudyRequest("list_sections", "chapter", "Fine-tuning"),
                )
        self.assertEqual(
            parse_study_request("Summarize Core idea"),
            StudyRequest("summarize", "named", "Core idea"),
        )
        for question in (
            "Explain this PDF",
            "Summarize the whole paper.",
            "Review the selected document",
        ):
            with self.subTest(question=question):
                self.assertEqual(
                    parse_study_request(question),
                    StudyRequest("summarize", "book", ""),
                )
        self.assertEqual(
            parse_study_request(
                "Turn the model-deployment chapter into an interview review, "
                "not a section summary."
            ),
            StudyRequest("summarize", "chapter", "model-deployment"),
        )
        self.assertEqual(
            parse_study_request("what chapters does this book have"),
            StudyRequest("list_chapters", "book", ""),
        )
        self.assertEqual(
            parse_study_request(
                "what are the chapters in the System Design Interview book"
            ),
            StudyRequest(
                "list_chapters",
                "book",
                "System Design Interview",
            ),
        )
        self.assertEqual(
            parse_study_request("show me the chapters from @[Sample Book]"),
            StudyRequest("list_chapters", "book", "Sample Book"),
        )
        with self.assertRaises(UnsupportedStudyRequestError):
            parse_study_request("Tell me something interesting")

    def test_a_bare_mention_summarizes_the_whole_document(self) -> None:
        """A reader who writes "summarize @[Title]" means the whole document.

        Without the mention-only branch this parsed as a *named* scope, so the
        title was resolved as if it were a section, nothing matched, and the
        turn died with "hierarchy route requires a canonical scope" instead of
        summarizing the paper.
        """

        for question in (
            "summarize @[Attention Is All You Need]",
            "Explain @[Attention Is All You Need]",
            "review @[Attention Is All You Need].",
        ):
            with self.subTest(question=question):
                self.assertEqual(
                    parse_study_request(question),
                    StudyRequest(
                        "summarize",
                        "book",
                        "",
                        book_reference="Attention Is All You Need",
                    ),
                )
        # The explicit noun forms keep working and still win over the bare
        # branch, and a mention inside a question is still a retrieval query.
        self.assertEqual(
            parse_study_request("summarize this paper @[Attention Is All You Need]"),
            StudyRequest(
                "summarize", "book", "", book_reference="Attention Is All You Need"
            ),
        )
        with self.assertRaises(UnsupportedStudyRequestError):
            parse_study_request("what does @[Attention Is All You Need] say about heads?")

    def test_maps_named_request_to_the_exact_section_subtree(self) -> None:
        request = parse_study_request("Summarize Core idea")
        scope = resolve_study_request(
            self.connection,
            request,
            owner_id=self.owner_id,
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
            "SELECT MIN(id) AS id FROM content_blocks WHERE owner_id = %s",
            (self.owner_id,),
        ).fetchone()["id"]
        for category in ("Header", "DetectedHeader", "DetectedFooter"):
            with self.subTest(category=category):
                self.connection.execute(
                    """
                    UPDATE content_blocks SET category = %s
                    WHERE id = %s AND owner_id = %s
                    """,
                    (category, first_block_id, self.owner_id),
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
            f"[N{node_id}:P{page}]" for node_id, page in node_pages.items()
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
        self.assertFalse(result.validation.warnings)
        self.assertIsNotNone(model.messages)
        self.assertIn(context.text, model.messages[1][1])
        self.assertIn(
            "shorten items 2, 4, 5, and 6",
            model.messages[1][1],
        )
        for node_id, page in context.allowed_citations:
            self.assertIn(
                f"[N{node_id}:P{page}]",
                model.messages[1][1],
            )
        self.assertIn(
            "never combine a node ID with a page",
            model.messages[0][1],
        )
        self.assertNotRegex(context.text, r"\[N\d+:P\d+:B\d+]")

    def test_records_model_finish_reason(self) -> None:
        scope, context = self._chapter_context()
        citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id in sorted(context.expected_node_ids)
            for candidate_node, page in sorted(context.allowed_citations)
            if candidate_node == node_id
        )

        class ModelWithMetadata:
            def invoke(self, messages):
                del messages
                return SimpleNamespace(
                    content=f"Grounded summary. {citations}",
                    response_metadata={"finish_reason": "stop"},
                )

        result = summarize_scope(
            ModelWithMetadata(),
            scope=scope,
            context=context,
        )

        self.assertEqual(result.finish_reason, "stop")

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

    def test_fullwidth_brackets_are_normalised_to_the_marker_contract(self):
        """The 2026-09-04 failure: grounding lost to a bracket glyph.

        An interview answer came back with twenty-five `\u3010S1\u3011` markers and
        no `[S1]`. The extractor matched none, the turn was stored with an
        empty citation list, and the interface showed the markers as literal
        text. The answer read as complete and was ungrounded — which is the
        worst shape this failure could take.
        """

        normalized = normalize_citation_syntax(
            "Exact search is impractical. \u3010S6\u3011 Ranking matters. \uff3bS2\uff3d"
        )

        self.assertEqual(
            normalized,
            "Exact search is impractical. [S6] Ranking matters. [S2]",
        )

    def test_ascii_markers_are_left_exactly_as_they_are(self):
        text = "Already correct. [S1] And a node marker. [N81:P153]"
        self.assertEqual(normalize_citation_syntax(text), text)

    def test_grouped_citation_syntax_is_split_without_changing_values(self):
        normalized = normalize_citation_syntax(
            "Two claims. [N81:P153; N81:P154] Third. [N82:P155]"
        )

        self.assertEqual(
            normalized,
            "Two claims. [N81:P153] [N81:P154] Third. [N82:P155]",
        )

    def test_provider_citation_syntax_is_normalized_for_all_marker_contracts(self):
        normalized = normalize_citation_syntax(
            "Claim. \ue200cite\ue202S1\ue202S3\ue201 "
            "Summary. \ue200cite\ue202N81:P153\ue202N82:P155\ue201"
        )

        self.assertEqual(
            normalized,
            "Claim. [S1] [S3] Summary. [N81:P153] [N82:P155]",
        )

    def test_invalid_citation_gets_one_feedback_driven_repair(self) -> None:
        scope, context = self._chapter_context()
        allowed = sorted(context.allowed_citations)
        first_node, _ = allowed[0]
        other_page = next(page for node_id, page in allowed if node_id != first_node)
        valid_citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id in sorted(context.expected_node_ids)
            for candidate_node, page in allowed
            if candidate_node == node_id
        )
        invalid = f"Bad citation. [N{first_node}:P{other_page}]"
        repaired = f"Complete repaired summary. {valid_citations}"
        model = SequenceSummaryModel(invalid, repaired)

        result = summarize_scope_with_repair(
            model,
            scope=scope,
            context=context,
        )

        self.assertTrue(result.validation.valid)
        self.assertEqual(result.attempt_count, 2)
        self.assertTrue(result.initial_errors)
        self.assertEqual(len(model.messages), 2)
        second_prompt = model.messages[1][1][1]
        self.assertIn("previous draft was rejected", second_prompt)
        self.assertIn(
            f"[N{first_node}:P{other_page}]",
            second_prompt,
        )

    def test_valid_summary_does_not_trigger_repair(self) -> None:
        scope, context = self._chapter_context()
        citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id in sorted(context.expected_node_ids)
            for candidate_node, page in sorted(context.allowed_citations)
            if candidate_node == node_id
        )
        model = SequenceSummaryModel(f"Valid summary. {citations}")

        result = summarize_scope_with_repair(
            model,
            scope=scope,
            context=context,
        )

        self.assertTrue(result.validation.valid)
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(model.messages), 1)

    def test_coverage_repair_retains_full_source_and_existing_answer(self) -> None:
        scope, context = self._chapter_context()
        allowed = sorted(context.allowed_citations)
        root = scope.root_node_id
        initial = "Training details and results are already explained. " + " ".join(
            f"[N{node}:P{page}]" for node, page in allowed if node != root)
        marker = next(f"[N{node}:P{page}]" for node, page in allowed if node == root)
        model = SequenceSummaryModel(initial, "Source overview with grounded context. " + marker)
        result = summarize_scope_with_repair(model, scope=scope, context=context)
        self.assertTrue(result.validation.valid)
        self.assertEqual(len(model.messages), 2)
        repair = model.messages[1][1][1]
        self.assertIn(context.text, repair)
        self.assertIn(initial, repair)

    def test_summary_repair_path_never_uses_truncation_prone_stream(self) -> None:
        scope, context = self._chapter_context()
        citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id in sorted(context.expected_node_ids)
            for candidate_node, page in sorted(context.allowed_citations)
            if candidate_node == node_id
        )

        class DivergentStreamingModel:
            def __init__(self):
                self.invoke_calls = 0
                self.stream_calls = 0

            def invoke(self, messages):
                del messages
                self.invoke_calls += 1
                return SimpleNamespace(
                    content=f"Complete summary. {citations}",
                    response_metadata={"finish_reason": "stop"},
                )

            def stream(self, messages):
                del messages
                self.stream_calls += 1
                yield SimpleNamespace(content="Truncated draft without citations.")

        model = DivergentStreamingModel()

        result = summarize_scope_with_repair(
            model,
            scope=scope,
            context=context,
        )

        self.assertTrue(result.validation.valid)
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(model.invoke_calls, 1)
        self.assertEqual(model.stream_calls, 0)

    def test_missing_recap_node_is_a_warning_not_an_error(self) -> None:
        scope, context = self._chapter_context()
        recap_id = max(scope.node_ids) + 1
        recap = ScopeNode(
            id=recap_id,
            book_id=scope.book_id,
            parent_id=scope.root_node_id,
            toc_index=max(node.toc_index for node in scope.nodes) + 1,
            level=2,
            node_type="section",
            title="Summary",
            path_text=f"{scope.display_path} :: Summary",
            start_page=5,
            end_page=5,
        )
        scope = replace(scope, nodes=(*scope.nodes, recap))
        context = replace(
            context,
            expected_node_ids=context.expected_node_ids.union({recap_id}),
            allowed_citations=context.allowed_citations.union({(recap_id, 5)}),
        )
        citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id in sorted(context.expected_node_ids - {recap_id})
            for candidate_node, page in sorted(context.allowed_citations)
            if candidate_node == node_id
        )

        validation = validate_summary(
            f"Grounded chapter summary. {citations}",
            scope=scope,
            context=context,
        )

        self.assertTrue(validation.valid)
        self.assertFalse(validation.errors)
        self.assertEqual(validation.missing_node_ids, frozenset({recap_id}))
        self.assertTrue(
            any(
                "optional supporting sections" in warning
                for warning in validation.warnings
            )
        )

    def test_interview_review_treats_lab_descendants_as_optional(self) -> None:
        scope, context = self._chapter_context()
        lab_id = max(scope.node_ids) + 1
        lab = ScopeNode(
            id=lab_id,
            book_id=scope.book_id,
            parent_id=scope.root_node_id,
            toc_index=max(node.toc_index for node in scope.nodes) + 1,
            level=2,
            node_type="section",
            title="5.3 Lab: Cross-Validation",
            path_text=f"{scope.display_path} :: 5.3 Lab: Cross-Validation",
            start_page=5,
            end_page=5,
        )
        scope = replace(scope, nodes=(*scope.nodes, lab))
        context = replace(
            context,
            expected_node_ids=context.expected_node_ids.union({lab_id}),
            allowed_citations=context.allowed_citations.union({(lab_id, 5)}),
        )
        citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id in sorted(context.expected_node_ids - {lab_id})
            for candidate_node, page in sorted(context.allowed_citations)
            if candidate_node == node_id
        )

        interview = validate_summary(
            f"Grounded interview review. {citations}",
            scope=scope,
            context=context,
            response_depth="interview",
        )
        deep = validate_summary(
            f"Grounded deep review. {citations}",
            scope=scope,
            context=context,
            response_depth="deep",
        )

        self.assertTrue(interview.valid)
        self.assertEqual(interview.optional_missing_node_ids, frozenset({lab_id}))
        self.assertFalse(deep.valid)
        self.assertEqual(deep.required_missing_node_ids, frozenset({lab_id}))

    def test_missing_coverage_gets_a_targeted_addendum(self) -> None:
        scope, context = self._chapter_context()
        ordered_ids = [
            node.id for node in scope.nodes if node.id in context.expected_node_ids
        ]
        missing_id = ordered_ids[-1]
        initial_citations = " ".join(
            f"[N{node_id}:P{page}]"
            for node_id in ordered_ids[:-1]
            for candidate_node, page in sorted(context.allowed_citations)
            if candidate_node == node_id
        )
        missing_page = next(
            page for node_id, page in context.allowed_citations if node_id == missing_id
        )
        model = SequenceSummaryModel(
            f"Initial safe summary. {initial_citations}",
            f"Missing interview point. [N{missing_id}:P{missing_page}]",
        )

        result = summarize_scope_with_repair(
            model,
            scope=scope,
            context=context,
        )

        self.assertTrue(result.validation.valid)
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("## Additional interview points", result.text)
        repair_system, repair_human = model.messages[1]
        self.assertIn(
            "Write only a concise Markdown coverage addendum", repair_system[1]
        )
        self.assertIn(f"Node {missing_id}", repair_human[1])
        self.assertTrue(result.text.startswith(f"Initial safe summary. {initial_citations}"))
        for covered_id in ordered_ids[:-1]:
            self.assertIn(f"## Node {covered_id}:", repair_human[1])

    def test_full_source_repair_checks_context_budget_before_model_call(self) -> None:
        scope, context = self._chapter_context()
        missing = max(context.expected_node_ids)
        initial = "Previously covered details. " * 1500 + " ".join(
            f"[N{node}:P{page}]"
            for node, page in sorted(context.allowed_citations)
            if node != missing
        )
        model = SequenceSummaryModel(initial, "Must not be called.")
        with self.assertRaises(ContextWindowExceededError):
            summarize_scope_with_repair(
                model, scope=scope, context=context,
                config=SummaryConfig(
                    context_window_tokens=12000, max_output_tokens=8000,
                    safety_margin_tokens=1000,
                ),
            )
        self.assertEqual(len(model.messages), 1)

    def test_returns_best_citation_safe_draft_when_coverage_repair_stalls(
        self,
    ) -> None:
        scope, context = self._chapter_context()
        ordered_ids = [
            node.id for node in scope.nodes if node.id in context.expected_node_ids
        ]
        missing_id = ordered_ids[-1]
        covered_id = ordered_ids[0]
        covered_page = next(
            page for node_id, page in context.allowed_citations if node_id == covered_id
        )
        safe_but_incomplete = f"Verified point. [N{covered_id}:P{covered_page}]"
        model = SequenceSummaryModel(
            safe_but_incomplete,
            safe_but_incomplete,
            safe_but_incomplete,
        )

        result = summarize_scope_with_repair(
            model,
            scope=scope,
            context=context,
        )

        self.assertTrue(result.validation.citation_safe)
        self.assertFalse(result.validation.coverage_complete)
        self.assertIn(missing_id, result.validation.required_missing_node_ids)
        self.assertEqual(result.attempt_count, 3)

    def test_appends_exact_references_for_citations_used(self) -> None:
        scope, _ = self._chapter_context()
        root, section = scope.nodes[:2]
        summary = (
            f"First claim. [N{root.id}:P{root.start_page}] "
            f"Repeated. [N{root.id}:P{root.start_page}] "
            f"Second claim. [N{section.id}:P{section.start_page}]"
        )

        rendered = append_references(summary, scope=scope)

        self.assertIn("## References", rendered)
        self.assertEqual(
            rendered.count(f"- [N{root.id}:P{root.start_page}]"),
            1,
        )
        self.assertIn(
            f"Sample Book → {root.path_text} — PDF p. {root.start_page}",
            rendered,
        )
        self.assertIn(
            (
                f"Sample Book → "
                f"{section.path_text.replace(' :: ', ' → ')} "
                f"— PDF p. {section.start_page}"
            ),
            rendered,
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

    def test_outline_of_a_chapter_without_subsections_says_so(self) -> None:
        """A flat embedded outline lists chapters only.

        Regression: the outline rendered a bare "Sections:" heading over an
        empty list for such a chapter instead of telling the reader there is
        nothing to list.
        """

        from study.scope import ResolvedScope, ScopeNode

        chapter = ScopeNode(
            id=1,
            book_id=1,
            parent_id=None,
            toc_index=0,
            level=1,
            node_type="chapter",
            title="CHAPTER 1: SCALE FROM ZERO TO MILLIONS OF USERS",
            path_text="CHAPTER 1: SCALE FROM ZERO TO MILLIONS OF USERS",
            start_page=5,
            end_page=33,
        )
        scope = ResolvedScope(
            kind="chapter",
            book_id=1,
            book_title="System Design Interview",
            root_node_id=1,
            display_path=chapter.path_text,
            start_page=5,
            end_page=33,
            nodes=(chapter,),
        )

        outline = format_outline(scope)

        self.assertNotIn("Sections:", outline)
        self.assertIn("lists no subsections", outline)
        self.assertIn("summary", outline)

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
