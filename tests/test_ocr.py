"""Transcribing a page, and deciding how far to trust what came back."""

import unittest

from ingestion.ocr import (
    BBOX_GRID,
    DEFAULT_RUN_THRESHOLD,
    FLAGGED,
    MINIMUM_REFERENCE_TOKENS,
    SUPPORTED,
    UNASSESSABLE,
    OcrBudget,
    OcrError,
    PageTranscription,
    assess_fabrication,
    parse_page_markup,
    prompt_hash,
    _strip_outer_fence,
)


def _prose(word_count: int, *, offset: int = 0) -> str:
    """Distinct multi-character words, so every token is comparable."""

    return " ".join(f"token{index + offset:04d}" for index in range(word_count))


class AssessFabricationTests(unittest.TestCase):
    def test_identical_readings_are_supported(self) -> None:
        text = _prose(80)
        assessment = assess_fabrication(text, text)
        self.assertEqual(assessment.verdict, SUPPORTED)
        self.assertEqual(assessment.longest_unsupported_run, 0)
        self.assertEqual(assessment.unsupported_ratio, 0.0)

    def test_scattered_word_level_noise_does_not_flag(self) -> None:
        """Two engines misreading individual words still agree on the page.

        This is the ordinary case for a scan, and flagging it would mean
        flagging everything.
        """

        reference = _prose(120)
        words = reference.split()
        for index in range(0, len(words), 4):
            words[index] = f"garbled{index}"
        candidate = " ".join(words)

        assessment = assess_fabrication(candidate, reference)
        self.assertEqual(assessment.verdict, SUPPORTED)
        self.assertLess(assessment.longest_unsupported_run, DEFAULT_RUN_THRESHOLD)
        self.assertGreater(assessment.unsupported_ratio, 0.0)

    def test_an_invented_passage_is_flagged(self) -> None:
        reference = _prose(120)
        candidate = f"{_prose(40)} {_prose(DEFAULT_RUN_THRESHOLD, offset=9000)}"

        assessment = assess_fabrication(candidate, reference)
        self.assertEqual(assessment.verdict, FLAGGED)
        self.assertGreaterEqual(
            assessment.longest_unsupported_run, DEFAULT_RUN_THRESHOLD
        )
        self.assertIn("token9000", assessment.sample)

    def test_a_failed_reference_is_unassessable_not_flagged(self) -> None:
        """A figure-only page defeats the deterministic engine.

        Silence from the reference is absence of evidence. Treating it as
        evidence of invention would flag exactly the diagram-heavy pages these
        books are full of.
        """

        assessment = assess_fabrication(_prose(200), "a b c")
        self.assertEqual(assessment.verdict, UNASSESSABLE)
        self.assertFalse(assessment.flagged)

    def test_reference_just_under_the_floor_is_unassessable(self) -> None:
        assessment = assess_fabrication(
            _prose(200), _prose(MINIMUM_REFERENCE_TOKENS - 1)
        )
        self.assertEqual(assessment.verdict, UNASSESSABLE)

    def test_markup_the_reference_cannot_contain_is_ignored(self) -> None:
        """The model is asked for HTML, LaTeX and comment-wrapped margins.

        None of that exists in a plain-text reading of the same pixels, so
        counting it as uncorroborated would flag every well-formed page.
        """

        reference = _prose(120)
        candidate = (
            "<!-- header: Chapter 9. Text-to-Image Generation -->\n"
            "<table><tr><td>Google PaLM</td><td>540B</td></tr></table>\n"
            "$$\\frac{\\partial L}{\\partial w} = \\sum_{i=1}^{n} x_i$$\n"
            f"{reference}\n"
            "<figure data-bbox=\"0.1,0.2,0.9,0.6\">Figure 9.3</figure>"
        )

        assessment = assess_fabrication(candidate, reference)
        self.assertEqual(assessment.verdict, SUPPORTED)

    def test_empty_candidate_is_unassessable(self) -> None:
        assessment = assess_fabrication("", _prose(120))
        self.assertEqual(assessment.verdict, UNASSESSABLE)

    def test_provenance_is_serializable(self) -> None:
        assessment = assess_fabrication(_prose(80), _prose(80))
        provenance = assessment.provenance()
        self.assertEqual(provenance["verdict"], SUPPORTED)
        self.assertIn("longest_unsupported_run", provenance)
        self.assertIn("comparable_tokens", provenance)


class OuterFenceTests(unittest.TestCase):
    def test_a_whole_page_wrapped_in_a_fence_is_unwrapped(self) -> None:
        self.assertEqual(
            _strip_outer_fence("```markdown\n# Proximity Service\n```"),
            "# Proximity Service",
        )

    def test_a_page_containing_a_code_listing_keeps_its_fences(self) -> None:
        page = "Consider these dicts\n\n```python\na = {'x': 1}\n```\n\nThe result."
        self.assertEqual(_strip_outer_fence(page), page)

    def test_unfenced_text_is_untouched(self) -> None:
        self.assertEqual(_strip_outer_fence("plain text"), "plain text")


class PromptHashTests(unittest.TestCase):
    def test_the_same_instruction_hashes_the_same(self) -> None:
        self.assertEqual(prompt_hash("read the page"), prompt_hash("read the page"))

    def test_a_changed_instruction_changes_the_hash(self) -> None:
        """Provenance has to distinguish readings made under different prompts."""

        self.assertNotEqual(prompt_hash("read the page"), prompt_hash("read it"))


def _transcription(cost: float) -> PageTranscription:
    return PageTranscription(
        page=1,
        text="text",
        provider="openrouter",
        model_id="test/vision-1",
        render_dpi=300,
        prompt_hash="abc123",
        cost_usd=cost,
    )


class OcrBudgetTests(unittest.TestCase):
    def test_pages_within_budget_are_charged(self) -> None:
        budget = OcrBudget(max_pages=10, max_cost_usd=1.0)
        for _ in range(5):
            budget.charge(_transcription(0.01))
        self.assertEqual(budget.pages_done, 5)
        self.assertAlmostEqual(budget.cost_usd, 0.05)

    def test_exceeding_the_page_cap_aborts(self) -> None:
        budget = OcrBudget(max_pages=2, max_cost_usd=100.0)
        budget.charge(_transcription(0.0))
        budget.charge(_transcription(0.0))
        with self.assertRaises(OcrError) as caught:
            budget.charge(_transcription(0.0))
        self.assertIn("page budget", str(caught.exception))

    def test_exceeding_the_cost_cap_aborts(self) -> None:
        """A retry loop over 778 pages is the failure worth engineering against."""

        budget = OcrBudget(max_pages=1000, max_cost_usd=0.05)
        budget.charge(_transcription(0.04))
        with self.assertRaises(OcrError) as caught:
            budget.charge(_transcription(0.04))
        self.assertIn("cost budget", str(caught.exception))

    def test_provenance_reports_what_was_spent(self) -> None:
        budget = OcrBudget(max_pages=10, max_cost_usd=1.0)
        budget.charge(_transcription(0.02))
        provenance = budget.provenance()
        self.assertEqual(provenance["pages_done"], 1)
        self.assertAlmostEqual(float(provenance["cost_usd"]), 0.02)


class PageMarkupTests(unittest.TestCase):
    """The contract between the generative stage and the deterministic one."""

    def test_running_margins_are_separated_from_the_body(self) -> None:
        markup = parse_page_markup(
            "<!-- header: Chapter 9 -->\n"
            "Diffusion models refine the image over many steps.\n"
            "<!-- footer: 288 | Chapter 9. Text-to-Image Generation -->"
        )
        self.assertEqual(markup.header, "Chapter 9")
        self.assertEqual(markup.footer, "288 | Chapter 9. Text-to-Image Generation")
        self.assertNotIn("footer:", markup.body)
        self.assertIn("Diffusion models", markup.body)

    def test_a_page_without_margins_yields_empty_strings(self) -> None:
        markup = parse_page_markup("Just body text.")
        self.assertEqual(markup.header, "")
        self.assertEqual(markup.footer, "")
        self.assertEqual(markup.body, "Just body text.")

    def test_a_thousand_grid_bbox_is_scaled_to_fractions(self) -> None:
        """Gemini reports boxes on a 0-1000 grid whatever the prompt asks for.

        Measured against a real page, the values are accurate and merely
        scaled, so both conventions are accepted rather than one enforced.
        """

        markup = parse_page_markup(
            '<figure data-bbox="169,441,852,706">Figure 1.1: Nearby search</figure>'
        )
        self.assertEqual(len(markup.figures), 1)
        figure = markup.figures[0]
        self.assertTrue(figure.locatable)
        assert figure.bbox is not None
        self.assertAlmostEqual(figure.bbox[0], 169 / BBOX_GRID)
        self.assertAlmostEqual(figure.bbox[3], 706 / BBOX_GRID)
        self.assertEqual(figure.caption, "Figure 1.1: Nearby search")

    def test_a_unit_bbox_is_kept_as_given(self) -> None:
        markup = parse_page_markup('<figure data-bbox="0.1,0.2,0.9,0.6">Fig</figure>')
        self.assertEqual(markup.figures[0].bbox, (0.1, 0.2, 0.9, 0.6))

    def test_a_figure_without_a_bbox_is_kept_but_not_locatable(self) -> None:
        """An unlocatable figure still exists; it just cites its whole page."""

        markup = parse_page_markup("<figure>Figure 3.2: Elman RNN</figure>")
        self.assertEqual(len(markup.figures), 1)
        self.assertFalse(markup.figures[0].locatable)

    def test_an_inverted_or_malformed_bbox_is_refused(self) -> None:
        for box in ("900,700,100,400", "1,2,3", "left,top,right,bottom", "-5,0,10,20"):
            with self.subTest(box=box):
                markup = parse_page_markup(f'<figure data-bbox="{box}">Fig</figure>')
                self.assertFalse(markup.figures[0].locatable)

    def test_figures_are_removed_from_the_body(self) -> None:
        """A caption indexed twice would double-count in retrieval."""

        markup = parse_page_markup(
            "Before.\n<figure data-bbox=\"1,2,3,4\">Figure 1.1</figure>\nAfter."
        )
        self.assertNotIn("Figure 1.1", markup.body)
        self.assertIn("Before.", markup.body)
        self.assertIn("After.", markup.body)


class PageTranscriptionTests(unittest.TestCase):
    def test_provenance_carries_everything_needed_to_reproduce_a_reading(self) -> None:
        provenance = _transcription(0.004).provenance()
        for key in ("provider", "model_id", "render_dpi", "prompt_hash"):
            self.assertIn(key, provenance)


if __name__ == "__main__":
    unittest.main()
