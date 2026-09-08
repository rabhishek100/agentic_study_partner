"""A sheet that is good but not perfect must still reach the reader.

The reviewer is a quality signal, not a gate. Three chapters in a row produced
nothing at all — each time the sheet existed, rendered, and was refused because
the reviewer wanted more of a dense chapter than the paper had room for. An
imperfect sheet is worth more than an error message, provided the reader is
told plainly where it is thin.

What stays fatal is anything that means there is no usable artifact, or a
misleading one: a citation marker that does not exist, an item ID nothing maps
to, a render that will not fit, or a render that dropped an item.
"""

import unittest
from unittest.mock import patch

from revision_sheets.contracts import RevisionError
from revision_sheets.validate import validate_sheet


class DensityIsAdvisoryTests(unittest.TestCase):
    """The word cap is a proxy; the renderer measures the real thing."""

    def sheet_with_words(self, words: int):
        import sys
        sys.path.insert(0, "tests")
        from test_revision_sheets import fixture

        sheet = fixture()
        sheet.essential_notes[0].text = " ".join(["word"] * words)
        return sheet

    def validate(self, sheet):
        import sys
        sys.path.insert(0, "tests")
        from test_revision_sheets import source_fixture

        source = source_fixture()
        return validate_sheet(
            sheet,
            allowed=set(source.references),
            units=source.units,
            figure_ids=set(sheet.diagram.source_figure_ids),
            scope_kind=source.request.scope_kind,
        )

    def test_an_overlong_sheet_is_reported_and_not_rejected(self) -> None:
        with patch.dict("os.environ", {"REVISION_MAX_PAGES": "2"}):
            advisories = self.validate(self.sheet_with_words(2000))

        self.assertTrue(advisories)
        self.assertIn("words", advisories[0])

    def test_a_sheet_within_the_budget_reports_nothing(self) -> None:
        self.assertEqual(self.validate(self.sheet_with_words(10)), [])

    def test_a_citation_that_does_not_exist_is_still_fatal(self) -> None:
        """Density may slide. Grounding may not."""

        sheet = self.sheet_with_words(10)
        sheet.essential_notes[0].citations = ["[N999:P999]"]

        with self.assertRaises(RevisionError) as caught:
            self.validate(sheet)
        self.assertEqual(caught.exception.code, "invalid_content")


class ReviewDoesNotGateTests(unittest.TestCase):
    """After its revisions are spent, the reviewer informs rather than blocks."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, "tests")
        from revision_sheets.review import (
            Inventory, EvidenceConcept, Review, Score, Coverage,
        )
        from test_revision_sheets import fixture

        self.concept = EvidenceConcept(
            id="c15", label="Quorum consistency", explanation="Quorum",
            citations=["[N1:P1]"], importance="essential",
        )
        self.inventory = Inventory(
            concepts=[self.concept], contradictions=[], source_coverage=[]
        )
        good = Score(score=4, rationale="Fine")
        # The real Review, deciding with its real rules: a covered row passes,
        # an uncovered essential concept fails. Using the genuine logic rather
        # than a stub is what makes this a test of the gate and not of a mock.
        self.passing = Review(
            beauty=good, presentation=good, concept_coverage=good, conciseness=good,
            coverage=[Coverage(concept_id="c15", item_ids=[fixture().central_idea.id],
                               status="covered", reason="Covered")],
            unsupported_claims=[], unresolved_contradictions=[], revision_instructions=[],
        )
        self.failing = Review(
            beauty=good, presentation=good, concept_coverage=good, conciseness=good,
            coverage=[Coverage(concept_id="c15", item_ids=[], status="partial",
                               reason="Only partially covered")],
            unsupported_claims=[], unresolved_contradictions=[], revision_instructions=[],
        )
        patcher = patch("revision_sheets.generate.make_inventory", return_value=self.inventory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def generate_with(self, review):
        import sys
        sys.path.insert(0, "tests")
        from test_revision_sheets import fixture, source_fixture, SequenceModel
        from revision_sheets.generate import generate

        calls = []

        def judge(*args, **kwargs):
            calls.append(True)
            return review

        model = SequenceModel(*[fixture() for _ in range(4)])
        with patch("revision_sheets.generate.judge_sheet", side_effect=judge):
            sheet, pdf, provenance = generate(
                source_fixture(), model=model, images=([], [], []),
                review_clients=(None, None, None),
            )
        return pdf, provenance, len(calls)

    def test_an_unsatisfied_review_publishes_with_its_findings(self) -> None:
        pdf, provenance, judged = self.generate_with(self.failing)

        # A sheet, a PDF, and an honest record of what is still missing.
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(provenance["quality_repairs"], 2)
        self.assertEqual(len(provenance["outstanding_findings"]), 1)
        self.assertIn("Quorum consistency", provenance["outstanding_findings"][0])
        # It revised twice before settling, rather than giving up at the first
        # complaint or looping forever.
        self.assertEqual(judged, 3)

    def test_a_satisfied_review_records_no_gaps(self) -> None:
        _, provenance, judged = self.generate_with(self.passing)

        self.assertEqual(provenance["outstanding_findings"], [])
        self.assertEqual(judged, 1)

    def test_the_page_count_is_measured_rather_than_assumed(self) -> None:
        import pymupdf

        pdf, provenance, _ = self.generate_with(self.passing)

        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            self.assertEqual(provenance["page_count"], len(document))


if __name__ == "__main__":
    unittest.main()
