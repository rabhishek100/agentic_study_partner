"""The options `hi_res` is asked for.

These are the settings that decide what canonical content a book ends up
holding, and how long a parse takes. The measured cost of each is recorded in
`docs/parser-performance.md`; these tests pin the settings themselves, since a
silent change to any of them changes every book ingested afterwards.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from parsing.parser import BLOCK_OCR, FULL_PAGE_OCR, _partition, ocr_mode


class PartitionOptionTests(unittest.TestCase):
    def _options(self, strategy: str) -> dict:
        with patch("parsing.parser.partition_pdf", return_value=[]) as partition:
            _partition(Path("book.pdf"), strategy)
        return partition.call_args.kwargs

    def test_hi_res_asks_for_tables_and_image_payloads(self):
        options = self._options("hi_res")

        self.assertTrue(options["infer_table_structure"])
        self.assertEqual(options["extract_image_block_types"], ["Image"])
        self.assertTrue(options["extract_image_block_to_payload"])

    def test_hi_res_ocrs_only_what_the_text_layer_misses(self):
        # Full-page OCR is half the per-page cost and re-reads text pdfminer
        # has already extracted from the file.
        self.assertEqual(self._options("hi_res")["ocr_mode"], BLOCK_OCR)

    def test_the_cheap_strategy_carries_no_layout_options(self):
        options = self._options("fast")

        self.assertEqual(set(options), {"filename", "strategy"})


class OcrModeTests(unittest.TestCase):
    def test_block_ocr_is_the_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(ocr_mode(), BLOCK_OCR)

    def test_full_page_ocr_can_be_restored(self):
        with patch.dict("os.environ", {"PARSER_FULL_PAGE_OCR": "1"}):
            self.assertEqual(ocr_mode(), FULL_PAGE_OCR)

    def test_any_other_value_leaves_the_default_alone(self):
        for value in ("0", "", "true", "yes"):
            with self.subTest(value=value):
                with patch.dict("os.environ", {"PARSER_FULL_PAGE_OCR": value}):
                    self.assertEqual(ocr_mode(), BLOCK_OCR)


if __name__ == "__main__":
    unittest.main()
