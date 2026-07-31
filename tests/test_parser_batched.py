"""Page-batched parallel extraction.

The speed claim is measured on the deployed worker by
`scripts/benchmark_parse.py`. These tests pin the properties that must hold
however the batches are scheduled: every page parsed exactly once, elements
in page order, and progress reported as batches land.
"""

import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import fitz

from parsing.parser import _subset_range, extract_batched
from tests.pdf_fixtures import structured_pdf


class _InlineExecutor:
    """Run submissions in this process.

    A patched batch function cannot be pickled to a child, and process
    behaviour is what scripts/benchmark_parse.py measures. These tests are
    about batching, ordering, progress, and failure handling.
    """

    def __init__(self, max_workers=None):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def submit(self, function, *args, **kwargs):
        future: Future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except BaseException as error:  # noqa: BLE001 - mirrored to the caller
            future.set_exception(error)
        return future


class _Metadata:
    def __init__(self, page_number):
        self.page_number = page_number


class _Element:
    def __init__(self, page_number):
        self.metadata = _Metadata(page_number)
        self.text = f"page {page_number}"


def fake_parse_range(task):
    """Stand in for a worker process: report the pages it was given."""

    source, first, last, workspace = task
    from unstructured.staging.base import elements_to_json

    elements = [_Element(page + 1) for page in range(first, last + 1)]
    destination = Path(workspace) / f"batch-{first:05d}.json"
    # The real worker writes JSON; keep the same contract without needing
    # Unstructured's element classes.
    destination.write_text(
        "[" + ",".join(f'{{"page": {e.metadata.page_number}}}' for e in elements) + "]"
    )
    del elements_to_json, source
    return first, str(destination)


def fake_load(filename):
    import json

    return [_Element(entry["page"]) for entry in json.loads(Path(filename).read_text())]


class BatchedExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="batched-test-"))
        self.addCleanup(self._remove)
        self.source = structured_pdf(self.directory / "book.pdf", page_count=6)

    def _remove(self):
        for path in sorted(self.directory.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.directory.rmdir()

    def _run(self, **kwargs):
        with (
            patch("parsing.parser.ProcessPoolExecutor", _InlineExecutor),
            patch("parsing.parser._parse_page_range", side_effect=fake_parse_range),
            patch("parsing.parser.elements_from_json", side_effect=fake_load),
        ):
            return extract_batched(self.source, **kwargs)

    def test_every_page_is_parsed_exactly_once_and_stays_in_order(self):
        elements = self._run(batch_pages=2, workers=3)

        self.assertEqual(
            [element.metadata.page_number for element in elements], [1, 2, 3, 4, 5, 6]
        )

    def test_batches_that_finish_out_of_order_are_reassembled_in_page_order(self):
        # A late first batch must not push its pages to the end of the book.
        original = fake_parse_range
        order = []

        def reversed_completion(task):
            order.append(task[1])
            return original(task)

        with (
            patch("parsing.parser.ProcessPoolExecutor", _InlineExecutor),
            patch("parsing.parser._parse_page_range", side_effect=reversed_completion),
            patch("parsing.parser.elements_from_json", side_effect=fake_load),
        ):
            elements = extract_batched(self.source, batch_pages=2, workers=3)

        self.assertEqual(
            [element.metadata.page_number for element in elements], [1, 2, 3, 4, 5, 6]
        )

    def test_progress_is_reported_once_per_batch(self):
        seen = []

        self._run(batch_pages=2, workers=3, on_batch=lambda done, total: seen.append((done, total)))

        # Three batches of two pages, reported as each lands.
        self.assertEqual(sorted(seen), [(1, 3), (2, 3), (3, 3)])

    def test_a_pathological_page_uses_fallback_and_other_pages_stay_hi_res(self):
        seen_ranges = []

        def record_range(task):
            seen_ranges.append((task[1], task[2]))
            return fake_parse_range(task)

        with (
            patch("parsing.parser.ProcessPoolExecutor", _InlineExecutor),
            patch("parsing.parser._parse_page_range", side_effect=record_range),
            patch("parsing.parser.elements_from_json", side_effect=fake_load),
            patch("parsing.parser._vector_complexity_pages", return_value=[2]),
            patch(
                "parsing.parser._extract_vector_fallback_page",
                return_value=[_Element(3)],
            ) as fallback,
        ):
            elements = extract_batched(self.source, batch_pages=2, workers=3)

        self.assertEqual(seen_ranges, [(0, 1), (3, 4), (5, 5)])
        fallback.assert_called_once_with(self.source, 2)
        self.assertEqual(
            [element.metadata.page_number for element in elements],
            [1, 2, 3, 4, 5, 6],
        )

    def test_a_single_worker_parses_the_document_whole(self):
        with patch("parsing.parser._partition", return_value=[]) as partition:
            extract_batched(self.source, batch_pages=2, workers=1)

        partition.assert_called_once()
        self.assertEqual(partition.call_args.args[1], "hi_res")

    def test_a_document_smaller_than_one_batch_is_parsed_whole(self):
        with patch("parsing.parser._partition", return_value=[]) as partition:
            extract_batched(self.source, batch_pages=50, workers=4)

        partition.assert_called_once()

    def test_a_failing_batch_fails_the_parse(self):
        # A silently dropped batch would lose pages from the book.
        def explode(task):
            if task[1] == 2:
                raise RuntimeError("batch died")
            return fake_parse_range(task)

        with (
            patch("parsing.parser.ProcessPoolExecutor", _InlineExecutor),
            patch("parsing.parser._parse_page_range", side_effect=explode),
            patch("parsing.parser.elements_from_json", side_effect=fake_load),
        ):
            with self.assertRaises(RuntimeError):
                extract_batched(self.source, batch_pages=2, workers=3)


class RangeSubsetTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="range-test-"))
        self.addCleanup(self._remove)

    def _remove(self):
        for path in sorted(self.directory.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.directory.rmdir()

    def test_a_range_holds_exactly_its_pages(self):
        source = structured_pdf(self.directory / "book.pdf", page_count=6)

        subset = _subset_range(source, 2, 4, self.directory / "range.pdf")

        with fitz.open(subset) as document:
            self.assertEqual(document.page_count, 3)
            self.assertIn("Page 3", document[0].get_text())
            self.assertIn("Page 5", document[2].get_text())


if __name__ == "__main__":
    unittest.main()
