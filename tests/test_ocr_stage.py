"""The transcription stage: checkpoints, budgets, and the proposal it ends on."""

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from ingestion.config import IngestionLimits
from ingestion.errors import ErrorCode, IngestionError
from ingestion.jobs import create_job
from ingestion.ocr import FabricationAssessment, PageTranscription
from ingestion.ocr_stage import (
    MAXIMUM_PROPOSED_ENTRIES,
    propose_outline_from_transcription,
    require_proposable,
    transcribe_book,
)
from ingestion.ocr_store import (
    clear_pages,
    completed_pages,
    page_summary,
    record_page,
    transcribed_text,
)
from ingestion.preflight import validate_table_of_contents
from storage.database import connection as database_connection
from tests.pdf_fixtures import scanned_pdf
from tests.postgres import PostgresOwnerMixin


LIMITS = IngestionLimits(ocr_concurrency=2, ocr_render_dpi=72, max_ocr_cost_usd=1.0)


class RecordingProvider:
    """A stand-in transcription model: no network, and it counts its calls."""

    def __init__(
        self,
        *,
        model_id: str = "test/vision-1",
        prompt_hash: str = "hash-1",
        text: str | None = None,
        cost: float = 0.001,
        fail_on: set[int] | None = None,
    ) -> None:
        self.name = "recording"
        self.model_id = model_id
        self.prompt_hash = prompt_hash
        self._text = text
        self._cost = cost
        self._fail_on = fail_on or set()
        self.pages_read: list[int] = []

    def transcribe(self, image: bytes, mime_type: str, page: int) -> PageTranscription:
        del image, mime_type
        if page in self._fail_on:
            raise RuntimeError(f"provider refused page {page}")
        self.pages_read.append(page)
        return PageTranscription(
            page=page,
            text=self._text if self._text is not None else f"# Chapter {page}\n\nBody.",
            provider=self.name,
            model_id=self.model_id,
            render_dpi=72,
            prompt_hash=self.prompt_hash,
            input_tokens=100,
            output_tokens=50,
            cost_usd=self._cost,
        )


class OutlineProposalTests(unittest.TestCase):
    """The proposal is evidence for a reviewer, not a hierarchy."""

    def test_markdown_depth_is_translated_to_outline_level(self) -> None:
        """A book whose headings start at ## must still yield a level-1 root.

        Outline level is a position in a tree; Markdown depth is a typographic
        choice the page made. Copying one to the other produces an entry with
        no parent, which the hierarchy validator rejects.
        """

        entries = propose_outline_from_transcription(
            [
                (1, "## Proximity Service\n\nText."),
                (2, "### Step 1 - Understand the Problem"),
            ]
        )

        self.assertEqual(
            entries,
            [(1, "Proximity Service", 1), (2, "Step 1 - Understand the Problem", 2)],
        )

    def test_a_skipped_depth_does_not_orphan_an_entry(self) -> None:
        entries = propose_outline_from_transcription(
            [(1, "# Part One"), (2, "#### Deeply Nested")]
        )

        validate_table_of_contents(entries, page_count=10)
        self.assertEqual([level for level, _, _ in entries], [1, 2])

    def test_a_repeated_running_heading_is_not_a_new_section(self) -> None:
        entries = propose_outline_from_transcription(
            [(1, "# Data Preparation"), (2, "# Data Preparation"), (3, "# Sampling")]
        )

        self.assertEqual([title for _, title, _ in entries], ["Data Preparation", "Sampling"])

    def test_pages_never_move_backwards(self) -> None:
        """A page order the hierarchy validator would refuse is not proposed."""

        entries = propose_outline_from_transcription(
            [(5, "# Later"), (2, "# Earlier")]
        )

        validate_table_of_contents(entries, page_count=10)
        self.assertEqual([page for _, _, page in entries], [5, 5])

    def test_running_margins_are_not_mistaken_for_headings(self) -> None:
        entries = propose_outline_from_transcription(
            [(1, "<!-- header: # Not A Heading -->\n# Real Heading")]
        )

        self.assertEqual([title for _, title, _ in entries], ["Real Heading"])

    def test_a_repeated_heading_cannot_orphan_the_entry_after_it(self) -> None:
        """The skipped repeat must not advance the level it was skipped from.

        The depth stack tracks every heading seen, including repeats that are
        deliberately not emitted, so it can advance while nothing is emitted
        and leave the next entry two levels below its predecessor. The
        hierarchy validator refuses that. Measured on a real book whose every
        chapter follows the same interview template: 31 repeated headings
        produced six such jumps, which would have failed the job *after* a
        reviewer confirmed all 394 entries.
        """

        entries = propose_outline_from_transcription(
            [
                (1, "# Chapter One"),
                (2, "## Clarifying Requirements"),
                (3, "### Functional requirements"),
                (4, "# Chapter Two"),
                (5, "## Clarifying Requirements"),
                (6, "### Non-functional requirements"),
            ]
        )

        validate_table_of_contents(entries, page_count=10)
        levels = [level for level, _, _ in entries]
        for index in range(1, len(levels)):
            self.assertLessEqual(levels[index], levels[index - 1] + 1)

    def test_a_recurring_heading_is_never_a_chapter(self) -> None:
        """A chapter title is unique to its chapter; a template step is not.

        Measured on a book of case studies: eleven chapters each contain "Data
        Preparation", "Model Development" and "Evaluation". Taking heading
        depth at face value put those beside the real chapters, so `list the
        chapters` answered with 31 entries of which half were template steps.
        """

        pages = [
            (1, "# Gmail Smart Compose"),
            (2, "# Data Preparation"),
            (3, "# Model Development"),
            (40, "# Google Translate"),
            (41, "# Data Preparation"),
            (42, "# Model Development"),
        ]

        entries = propose_outline_from_transcription(pages)

        chapters = [title for level, title, _ in entries if level == 1]
        self.assertEqual(chapters, ["Gmail Smart Compose", "Google Translate"])

    def test_a_recurring_heading_is_kept_under_every_chapter(self) -> None:
        """Demoting it must not merge eleven real sections into one."""

        pages = [
            (1, "# Gmail Smart Compose"),
            (2, "# Data Preparation"),
            (40, "# Google Translate"),
            (41, "# Data Preparation"),
        ]

        entries = propose_outline_from_transcription(pages)

        preparations = [(l, p) for l, title, p in entries if title == "Data Preparation"]
        self.assertEqual(preparations, [(2, 2), (2, 41)])

    def test_the_same_heading_continuing_across_a_page_break_is_one_entry(self) -> None:
        entries = propose_outline_from_transcription(
            [(1, "# Sampling"), (2, "# Sampling"), (3, "# Ranking")]
        )

        self.assertEqual([title for _, title, _ in entries], ["Sampling", "Ranking"])

    def test_an_oversized_proposal_loses_depth_not_its_tail(self) -> None:
        """Cutting the tail leaves the end of the book unaddressable.

        Dropping the deepest level costs granularity evenly and keeps every
        chapter, which is the property that matters for citation.
        """

        pages = [(1, "# Opening")]
        for index in range(2, MAXIMUM_PROPOSED_ENTRIES + 200):
            pages.append((index, f"### Deep topic {index}"))
        pages.append((MAXIMUM_PROPOSED_ENTRIES + 400, "# Closing"))

        entries = propose_outline_from_transcription(pages)

        self.assertLessEqual(len(entries), MAXIMUM_PROPOSED_ENTRIES)
        titles = [title for _, title, _ in entries]
        self.assertIn("Opening", titles)
        self.assertIn("Closing", titles)

    def test_a_book_with_no_headings_fails_instead_of_parking_for_review(self) -> None:
        """An empty review queue entry wastes a reviewer's time and hides a bug."""

        with self.assertRaises(IngestionError) as caught:
            require_proposable([], page_count=10)
        self.assertEqual(
            caught.exception.code, ErrorCode.MISSING_TABLE_OF_CONTENTS
        )

    def test_a_proposal_past_the_last_page_is_clamped(self) -> None:
        entries = require_proposable([(1, "Chapter 1", 999)], page_count=10)
        self.assertEqual(entries, [(1, "Chapter 1", 10)])


class TranscriptionStageTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.addCleanup(self.tearDownPostgresOwner)
        self.source = scanned_pdf(Path(self._directory.name) / "scan.pdf", page_count=6)
        with database_connection(self.database_url) as connection:
            self.job, _ = create_job(
                connection,
                owner_id=self.owner_id,
                idempotency_key=uuid4(),
                original_filename="scan.pdf",
                content_type="application/pdf",
                content_length=1024,
            )

    def _connect(self):
        return database_connection(self.database_url)

    def _run(self, provider, *, limits: IngestionLimits = LIMITS, reference=None):
        return transcribe_book(
            self.source,
            owner_id=self.owner_id,
            job_id=self.job.id,
            limits=limits,
            provider=provider,
            reference=reference,
            open_connection=self._connect,
        )

    def test_every_page_is_transcribed_and_committed(self) -> None:
        provider = RecordingProvider()

        outcome = self._run(provider)

        self.assertEqual(outcome.pages_transcribed, 6)
        self.assertEqual(outcome.pages_reused, 0)
        self.assertEqual(outcome.summary.pages, 6)
        self.assertEqual(sorted(provider.pages_read), [1, 2, 3, 4, 5, 6])
        with self._connect() as connection:
            pages = transcribed_text(
                connection, owner_id=self.owner_id, job_id=self.job.id
            )
        self.assertEqual([page for page, _ in pages], [1, 2, 3, 4, 5, 6])

    def test_a_second_pass_re_reads_nothing(self) -> None:
        """The point of the checkpoint: a resumed job never pays twice."""

        first = RecordingProvider()
        self._run(first)

        second = RecordingProvider()
        outcome = self._run(second)

        self.assertEqual(second.pages_read, [])
        self.assertEqual(outcome.pages_transcribed, 0)
        self.assertEqual(outcome.pages_reused, 6)

    def test_a_changed_model_re_reads_every_page(self) -> None:
        """Checkpoints are keyed on the reading, not on the page number.

        Mixing pages from two engines would leave a book whose text came from
        both carrying one engine's provenance.
        """

        self._run(RecordingProvider(model_id="test/vision-1"))

        newer = RecordingProvider(model_id="test/vision-2")
        outcome = self._run(newer)

        self.assertEqual(sorted(newer.pages_read), [1, 2, 3, 4, 5, 6])
        self.assertEqual(outcome.pages_transcribed, 6)

    def test_a_changed_instruction_re_reads_every_page(self) -> None:
        self._run(RecordingProvider(prompt_hash="hash-1"))

        rewritten = RecordingProvider(prompt_hash="hash-2")
        self._run(rewritten)

        self.assertEqual(sorted(rewritten.pages_read), [1, 2, 3, 4, 5, 6])

    def test_a_failed_page_keeps_the_pages_already_paid_for(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run(RecordingProvider(fail_on={4, 5, 6}))

        with self._connect() as connection:
            summary = page_summary(
                connection, owner_id=self.owner_id, job_id=self.job.id
            )
        self.assertGreater(summary.pages, 0)
        self.assertLess(summary.pages, 6)

        # The retry reads only what is missing.
        resumed = RecordingProvider()
        self._run(resumed)
        self.assertEqual(len(resumed.pages_read), 6 - summary.pages)

    def test_the_cost_ceiling_aborts_the_job(self) -> None:
        """A retry loop over hundreds of pages is the failure worth stopping."""

        limits = IngestionLimits(
            ocr_concurrency=1, ocr_render_dpi=72, max_ocr_cost_usd=0.0025
        )

        with self.assertRaises(Exception) as caught:
            self._run(RecordingProvider(cost=0.001), limits=limits)
        self.assertIn("budget", str(caught.exception))

    def test_a_flagged_page_is_recorded_without_stopping_the_book(self) -> None:
        """The gate flags and never blocks."""

        # Long enough to clear the floor below which a reference is treated as
        # absent evidence rather than contrary evidence, and sharing no words
        # with the transcription.
        contrary = " ".join(f"reference{index:03d}" for index in range(40))

        class Unsupportive:
            name = "unsupportive"
            model_id = "reference-1"

            def transcribe(self, image, mime_type, page):
                del image, mime_type
                return PageTranscription(
                    page=page,
                    text=contrary,
                    provider=self.name,
                    model_id=self.model_id,
                    render_dpi=72,
                    prompt_hash="",
                )

        invented = " ".join(f"invented{index:03d}" for index in range(40))
        outcome = self._run(
            RecordingProvider(text=f"# Chapter\n\n{invented}"),
            reference=Unsupportive(),
        )

        self.assertEqual(outcome.summary.pages, 6)
        self.assertEqual(outcome.summary.flagged, 6)

    def test_a_reference_engine_that_fails_leaves_pages_unassessable(self) -> None:
        """A broken cross-check is not evidence against the transcription."""

        class Broken:
            name = "broken"
            model_id = "reference-broken"

            def transcribe(self, image, mime_type, page):
                raise RuntimeError("tesseract exploded")

        outcome = self._run(RecordingProvider(), reference=Broken())

        self.assertEqual(outcome.summary.pages, 6)
        self.assertEqual(outcome.summary.flagged, 0)
        self.assertEqual(outcome.summary.unassessable, 6)


class CheckpointStoreTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.setUpPostgresOwner()
        self.addCleanup(self.tearDownPostgresOwner)
        with database_connection(self.database_url) as connection:
            self.job, _ = create_job(
                connection,
                owner_id=self.owner_id,
                idempotency_key=uuid4(),
                original_filename="scan.pdf",
                content_type="application/pdf",
                content_length=1024,
            )

    def _record(self, page: int, *, text: str = "text", cost: float = 0.002) -> None:
        with database_connection(self.database_url) as connection:
            record_page(
                connection,
                owner_id=self.owner_id,
                job_id=self.job.id,
                transcription=PageTranscription(
                    page=page,
                    text=text,
                    provider="recording",
                    model_id="test/vision-1",
                    render_dpi=300,
                    prompt_hash="hash-1",
                    input_tokens=10,
                    output_tokens=20,
                    cost_usd=cost,
                ),
                fabrication=FabricationAssessment(verdict="supported"),
            )

    def test_re_recording_a_page_replaces_it(self) -> None:
        """A retry re-reading a page must replace it, not collide with it."""

        self._record(1, text="first reading")
        self._record(1, text="second reading")

        with database_connection(self.database_url) as connection:
            pages = transcribed_text(
                connection, owner_id=self.owner_id, job_id=self.job.id
            )
            summary = page_summary(
                connection, owner_id=self.owner_id, job_id=self.job.id
            )
        self.assertEqual(pages, [(1, "second reading")])
        self.assertEqual(summary.pages, 1)

    def test_completed_pages_are_scoped_to_the_reading(self) -> None:
        self._record(1)

        with database_connection(self.database_url) as connection:
            same = completed_pages(
                connection,
                owner_id=self.owner_id,
                job_id=self.job.id,
                model_id="test/vision-1",
                prompt_hash="hash-1",
            )
            other = completed_pages(
                connection,
                owner_id=self.owner_id,
                job_id=self.job.id,
                model_id="test/vision-2",
                prompt_hash="hash-1",
            )
        self.assertEqual(list(same), [1])
        self.assertEqual(other, {})

    def test_the_summary_totals_what_was_spent(self) -> None:
        self._record(1, cost=0.002)
        self._record(2, cost=0.003)

        with database_connection(self.database_url) as connection:
            summary = page_summary(
                connection, owner_id=self.owner_id, job_id=self.job.id
            )
        self.assertEqual(summary.pages, 2)
        self.assertAlmostEqual(summary.cost_usd, 0.005)
        self.assertEqual(summary.input_tokens, 20)

    def test_clearing_discards_checkpoints(self) -> None:
        self._record(1)
        self._record(2)

        with database_connection(self.database_url) as connection:
            removed = clear_pages(
                connection, owner_id=self.owner_id, job_id=self.job.id, pages=[1]
            )
            remaining = transcribed_text(
                connection, owner_id=self.owner_id, job_id=self.job.id
            )
        self.assertEqual(removed, 1)
        self.assertEqual([page for page, _ in remaining], [2])


if __name__ == "__main__":
    unittest.main()
