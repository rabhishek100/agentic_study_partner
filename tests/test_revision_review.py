"""Regression cases for independent review, complete figure accounting and HTML."""
import unittest
from unittest.mock import Mock, patch

import pymupdf

from revision_sheets.contracts import RevisionError
from revision_sheets.generate import read_all_figures, generate, Draft, SheetPatch, apply_sheet_patch
from revision_sheets.html_render import make_html, render_html_pdf
from revision_sheets.review import Coverage, EvidenceConcept, FigureBatch, FigureReading, Inventory, InventoryUnit, Review, Score, make_inventory
from tests.test_revision_sheets import fixture, source_fixture, SequenceModel


def inventory():
    return Inventory(concepts=[EvidenceConcept(id="durability", label="Durability", explanation="Acknowledgement follows storage.",
        citations=["[N1:P1]"], importance="essential")], contradictions=[], source_coverage=[])


def review(status="covered"):
    score = Score(score=4, rationale="Readable and grounded fixture")
    return Review(beauty=score, presentation=score, concept_coverage=score, conciseness=score,
        coverage=[Coverage(concept_id="durability", item_ids=[fixture().central_idea.id], status=status, reason="Evidence mapping")],
        unsupported_claims=[], unresolved_contradictions=[], revision_instructions=[])


class ReviewTests(unittest.TestCase):
    def test_high_scores_cannot_override_missing_concept_or_invalid_mapping(self):
        result = review("partial")
        self.assertTrue(result.failures(inventory(), fixture()))
        result = review(); result.coverage[0].item_ids = ["invented"]
        self.assertTrue(result.failures(inventory(), fixture()))
        result = review(); result.unresolved_contradictions = ["Prose 41.0 versus table 41.8 remains unqualified"]
        self.assertTrue(result.failures(inventory(), fixture()))
        self.assertFalse(review().failures(inventory(), fixture()))

    def test_inventory_repair_cannot_drop_concepts_or_source_units(self):
        source = source_fixture()
        complete = inventory()
        complete.source_coverage = [InventoryUnit(unit_id="u1", concept_ids=["durability"], supporting_only=False, reason="Mechanism")]
        self.assertEqual(make_inventory(source, [], Mock(invoke=Mock(return_value=complete))), complete)
        missing = complete.model_copy(deep=True)
        missing.source_coverage = []
        with self.assertRaisesRegex(RevisionError, "every required source unit"):
            make_inventory(source, [], Mock(invoke=Mock(return_value=missing)))
        invalid = complete.model_copy(deep=True)
        invalid.concepts.append(EvidenceConcept(id="second", label="Second mechanism", explanation="Retain this concept",
            citations=["[N1:P999]"], importance="essential"))
        with self.assertRaisesRegex(RevisionError, "dropped concepts"):
            make_inventory(source, [], Mock(invoke=Mock(side_effect=[invalid, complete])))

    def test_quality_patch_preserves_unedited_content_and_rejects_unknown_ids(self):
        original = fixture()
        edit = original.essential_notes[0].model_copy(update={"text": "Corrected mechanism with its condition."})
        result = apply_sheet_patch(original, SheetPatch(edits=[edit], diagram=None))
        self.assertEqual(result.essential_notes[0].text, edit.text)
        self.assertEqual(result.essential_notes[1:], original.essential_notes[1:])
        self.assertEqual(result.diagram, original.diagram)
        self.assertEqual(result.source_dispositions, original.source_dispositions)
        self.assertEqual(result.central_idea, original.central_idea)
        with self.assertRaisesRegex(RevisionError, "existing note IDs"):
            apply_sheet_patch(original, SheetPatch(edits=[edit.model_copy(update={"id": "unknown"})], diagram=None))

    def test_all_figures_are_inspected_in_batches_without_sampling(self):
        source = source_fixture()
        source.figures = [{"block_id": i, "node_id": 1, "page": 1} for i in range(25)]
        with pymupdf.open() as doc:
            page = doc.new_page(width=30, height=30)
            payload = page.get_pixmap().tobytes("png")
        client = Mock()
        client.invoke.side_effect = [FigureBatch(figures=[FigureReading(block_id=i, description="Original diagram", role="concept")
            for i in range(start, min(start + 4, 25))]) for start in range(0, 25, 4)]
        with patch("revision_sheets.generate.load_figure", return_value=payload):
            assets, readings = read_all_figures(source, client, lambda stage: None)
        self.assertEqual(set(assets), set(range(25)))
        self.assertEqual(len(readings), 25)
        self.assertEqual(client.invoke.call_count, 7)

    def test_failed_figure_is_not_silently_omitted(self):
        source = source_fixture(); source.figures = [{"block_id": 1, "node_id": 1, "page": 1}]
        with patch("revision_sheets.generate.load_figure", side_effect=ValueError("missing")):
            with self.assertRaisesRegex(RevisionError, "cannot be read"):
                read_all_figures(source, Mock(), lambda stage: None)

    def test_judge_revision_is_bounded_and_an_unsatisfied_review_still_publishes(self):
        """Revisions are bounded; running out of them no longer destroys the sheet.

        This previously asserted the opposite — that a review the sheet could
        not satisfy returned no artifact. Three dense chapters in a row then
        produced nothing at all, each time because the reviewer wanted more of
        the source than the paper had room for, and the reader was handed an
        error instead of a usable sheet. The bound is still two revisions; what
        changed is what happens when they are spent.
        """

        with patch("revision_sheets.generate.make_inventory", return_value=inventory()), \
             patch("revision_sheets.generate.judge_sheet", side_effect=[review("partial"), review()]):
            _, _, provenance = generate(source_fixture(), model=SequenceModel(fixture(), fixture()), review_clients=(None, None, None))
            self.assertEqual(provenance["quality_repairs"], 1)
            self.assertEqual(len(provenance["review_history"]), 2)
        model = SequenceModel(fixture(), fixture(), fixture())
        with patch("revision_sheets.generate.make_inventory", return_value=inventory()), \
             patch("revision_sheets.generate.judge_sheet", return_value=review("missing")):
            _, pdf, provenance = generate(source_fixture(), model=model, review_clients=(None, None, None))
        # Still bounded to two revisions, so an unsatisfiable reviewer cannot
        # spend the account on an endless loop.
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(provenance["quality_repairs"], 2)
        # And the reader gets the sheet, with what is still missing recorded on
        # it rather than discarded with it.
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(provenance["outstanding_findings"])

    def test_real_workflow_uses_targeted_quality_patch(self):
        author = SequenceModel(fixture())
        editor = Mock(invoke=Mock(return_value=SheetPatch(edits=[], diagram=None)))
        with patch("revision_sheets.generate.revision_model", side_effect=lambda schema=Draft, **kwargs: author if schema is Draft else editor), \
             patch("revision_sheets.generate.make_inventory", return_value=inventory()), \
             patch("revision_sheets.generate.judge_sheet", side_effect=[review("partial"), review()]):
            result, _, provenance = generate(source_fixture(), review_clients=(None, None, None))
        self.assertEqual(len(author.calls), 1)
        self.assertEqual(editor.invoke.call_count, 1)
        self.assertEqual(result, fixture())
        self.assertEqual(provenance["quality_repairs"], 1)

    def test_missing_essential_concept_recomposes_instead_of_existing_note_patch(self):
        author = SequenceModel(fixture(), fixture())
        editor = Mock(invoke=Mock(return_value=SheetPatch(edits=[], diagram=None)))
        with patch("revision_sheets.generate.revision_model", side_effect=lambda schema=Draft, **kwargs: author if schema is Draft else editor), \
             patch("revision_sheets.generate.make_inventory", return_value=inventory()), \
             patch("revision_sheets.generate.judge_sheet", side_effect=[review("missing"), review()]):
            _, pdf, provenance = generate(source_fixture(), review_clients=(None, None, None))
        self.assertEqual(len(author.calls), 2)
        self.assertEqual(editor.invoke.call_count, 0)
        self.assertEqual(provenance["quality_repairs"], 1)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertFalse(provenance["outstanding_findings"])

    def test_html_escapes_source_and_prints_at_most_two_pages_without_clipping(self):
        source = source_fixture(); sheet = fixture()
        html = make_html(sheet, source_title='<script>alert("x")</script>', scope_title=source.scope_title, references=source.references)
        self.assertNotIn('<script>', html)
        self.assertIn("&lt;script&gt;", html)
        pdf = render_html_pdf(html, sheet)
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            self.assertEqual(len(doc), 2)
            self.assertTrue(all(abs(p.rect.height - 841.89) < 1 for p in doc))
        sheet.essential_notes[0].text *= 50
        html = make_html(sheet, source_title=source.title, scope_title=source.scope_title, references=source.references)
        with self.assertRaisesRegex(RevisionError, "overlaps"):
            render_html_pdf(html, sheet)

    def test_layout_session_reuses_browser_but_closes_each_context_even_on_overflow(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from playwright.sync_api import sync_playwright as real_runtime
        from revision_sheets.html_render import pdf_render_session
        browsers, contexts = [], []

        @contextmanager
        def recorded_runtime():
            with real_runtime() as runtime:
                def launch(**kwargs):
                    real = runtime.chromium.launch(**kwargs)
                    browser = Mock(wraps=real)
                    def new_context(**options):
                        self.assertFalse(options['java_script_enabled'])
                        context = Mock(wraps=real.new_context(**options))
                        contexts.append(context)
                        return context
                    browser.new_context.side_effect = new_context
                    browsers.append(browser)
                    return browser
                yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        source = source_fixture(); sheet = fixture()
        valid = make_html(sheet, source_title=source.title, scope_title=source.scope_title, references=source.references)
        oversized = sheet.model_copy(deep=True)
        oversized.essential_notes[0].text *= 50
        invalid = make_html(oversized, source_title=source.title, scope_title=source.scope_title, references=source.references)
        with patch('playwright.sync_api.sync_playwright', recorded_runtime):
            with pdf_render_session() as browser:
                first = render_html_pdf(valid, sheet, browser=browser)
                with self.assertRaisesRegex(RevisionError, 'overlaps'):
                    render_html_pdf(invalid, oversized, browser=browser)
                last = render_html_pdf(valid, sheet, browser=browser)
                self.assertEqual(len(browsers), 1)
                self.assertEqual(len(contexts), 3)
                self.assertTrue(all(context.close.call_count == 1 for context in contexts))
                self.assertEqual(browser.close.call_count, 0)
            self.assertEqual(browser.close.call_count, 1)
        with pymupdf.open(stream=first, filetype='pdf') as before, pymupdf.open(stream=last, filetype='pdf') as after:
            self.assertEqual([page.get_text() for page in before], [page.get_text() for page in after])
            self.assertEqual(len(before), 2)
