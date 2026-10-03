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


class AdvisoriesDoNotSpendRepairsTests(unittest.TestCase):
    """An advisory must not eat the budget a real error needs.

    Making density advisory was right; routing it back to the model was not.
    It consumed the single content repair, so the very next draft the schema
    rejected had nothing left and failed the whole job — trading a cosmetic
    proxy for the artifact. Production hit exactly that: "The model could not
    produce a valid sheet schema after repair."
    """

    def test_an_overlong_sheet_goes_straight_to_the_renderer(self) -> None:
        import sys
        sys.path.insert(0, "tests")
        from test_revision_sheets import fixture, source_fixture, SequenceModel
        from revision_sheets.generate import generate
        from revision_sheets.review import (
            Inventory, EvidenceConcept, Review, Score, Coverage,
        )

        concept = EvidenceConcept(id="c1", label="Fixture", explanation="Fixture",
                                  citations=["[N1:P1]"], importance="essential")
        good = Score(score=4, rationale="Fine")
        review = Review(
            beauty=good, presentation=good, concept_coverage=good, conciseness=good,
            coverage=[Coverage(concept_id="c1", item_ids=[fixture().central_idea.id],
                               status="covered", reason="Covered")],
            unsupported_claims=[], unresolved_contradictions=[], revision_instructions=[],
        )
        model = SequenceModel(fixture())
        with (
            patch("revision_sheets.generate.make_inventory",
                  return_value=Inventory(concepts=[concept], contradictions=[], source_coverage=[])),
            patch("revision_sheets.generate.judge_sheet", return_value=review),
            patch("revision_sheets.generate.validate_sheet",
                  return_value=["The visible sheet has 2000 words."]),
        ):
            _, pdf, provenance = generate(
                source_fixture(), model=model, images=([], [], []),
                review_clients=(None, None, None),
            )

        self.assertTrue(pdf.startswith(b"%PDF"))
        # The model was asked once. The advisory did not send it back, and the
        # repair budget is untouched and available for a real schema failure.
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(provenance["content_repairs"], 0)
        self.assertEqual(provenance["advisories"], ["The visible sheet has 2000 words."])


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



class ReviewItemIdTests(unittest.TestCase):
    """The diagram has no ID in the sheet JSON; citing it as "diagram" is valid."""

    def test_diagram_reference_is_accepted_and_unknown_items_are_not(self) -> None:
        import sys
        sys.path.insert(0, "tests")
        from revision_sheets.review import Coverage, EvidenceConcept, Inventory, Review, Score
        from test_revision_sheets import fixture

        sheet = fixture()
        concept = EvidenceConcept(id="c1", label="Flow", explanation="Flow",
                                  citations=["[N1:P1]"], importance="essential")
        inventory = Inventory(concepts=[concept], contradictions=[], source_coverage=[])
        good = Score(score=4, rationale="Fine")

        def review(item_ids):
            return Review(beauty=good, presentation=good, concept_coverage=good, conciseness=good,
                          coverage=[Coverage(concept_id="c1", item_ids=item_ids, status="covered", reason="Covered")],
                          unsupported_claims=[], unresolved_contradictions=[], revision_instructions=[])

        self.assertEqual(review([sheet.central_idea.id, "diagram"]).failures(inventory, sheet), [])
        self.assertEqual(review(["diagram_description"]).failures(inventory, sheet), [])
        self.assertEqual(review(["not_a_rendered_item"]).failures(inventory, sheet),
                         ["Invalid rendered evidence for c1."])

    def test_reviewer_is_shown_the_valid_item_ids(self) -> None:
        import sys
        sys.path.insert(0, "tests")
        from revision_sheets.review import EvidenceConcept, Inventory, judge_sheet
        from test_revision_sheets import fixture, source_fixture

        sheet = fixture()
        inventory = Inventory(concepts=[EvidenceConcept(id="c1", label="Flow", explanation="Flow",
                              citations=["[N1:P1]"], importance="essential")], contradictions=[], source_coverage=[])
        captured = {}

        class Client:
            def invoke(self, messages):
                captured["text"] = messages[1].content[0]["text"]
                raise RuntimeError("stop after capture")

        import pymupdf
        document = pymupdf.open()
        document.new_page()
        pdf = document.tobytes()
        with self.assertRaises(RuntimeError):
            judge_sheet(source_fixture(), sheet, inventory, pdf, Client())
        self.assertIn("VALID COVERAGE ITEM IDS", captured["text"])
        self.assertIn('"diagram_description"', captured["text"])


class InvalidTargetedEditTests(unittest.TestCase):
    setUp = ReviewDoesNotGateTests.setUp

    def test_an_invalid_targeted_edit_falls_back_to_recomposition(self) -> None:
        import sys
        sys.path.insert(0, "tests")
        from revision_sheets.generate import Draft, generate
        from test_revision_sheets import fixture, source_fixture

        patch_calls, draft_calls = [], []

        class Client:
            def __init__(self, schema):
                self.schema = schema

            def invoke(self, messages):
                if self.schema.__name__ == "SheetPatch":
                    patch_calls.append(messages)
                    return self.schema.model_validate({})  # always invalid
                draft_calls.append(messages)
                return Draft(sheet=fixture(), missing_evidence="")

        with (patch("revision_sheets.generate.revision_model",
                    side_effect=lambda schema=Draft, **_: Client(schema)),
              patch("revision_sheets.generate.judge_sheet", side_effect=[self.failing, self.passing])):
            _, pdf, provenance = generate(source_fixture(), model=None, images=([], [], []),
                                          review_clients=(None, None, None))

        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(len(patch_calls), 1)
        self.assertEqual(len(draft_calls), 2)
        retry = draft_calls[-1][-1].content[0]["text"]
        self.assertIn("Independent review failures", retry)
        self.assertIn("targeted edit was invalid", retry)
        self.assertEqual(provenance["outstanding_findings"], [])


if __name__ == "__main__":
    unittest.main()
