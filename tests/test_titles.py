"""What a source is called when nobody has said."""

import unittest

from study.titles import (
    looks_machine_generated,
    readable_title,
    resolve_title,
)


class ReadableTitleTests(unittest.TestCase):
    def test_a_lecture_filename_becomes_words(self) -> None:
        self.assertEqual(
            readable_title("cme295-lecture1-h264.mp4"), "CME 295 Lecture 1"
        )

    def test_codec_and_resolution_noise_is_dropped(self) -> None:
        self.assertEqual(
            readable_title("Deep_Learning_Lecture_6_1080p_x264.mkv"),
            "Deep Learning Lecture 6",
        )

    def test_export_and_version_leftovers_are_dropped(self) -> None:
        self.assertEqual(
            readable_title("attention-is-all-you-need_v7_final.pdf"),
            "Attention Is All You Need",
        )

    def test_camel_case_is_read_as_separate_words(self) -> None:
        self.assertEqual(
            readable_title("AttentionIsAllYouNeed.pdf"), "Attention Is All You Need"
        )

    def test_an_authors_capitalisation_survives(self) -> None:
        self.assertEqual(readable_title("CS231n_lecture4.mp4"), "CS231n Lecture 4")

    def test_a_course_code_is_read_as_a_code_and_a_number(self) -> None:
        self.assertEqual(
            readable_title("cme295-lecture1-h264.mp4"), "CME 295 Lecture 1"
        )
        self.assertEqual(readable_title("ee364a_notes.pdf"), "EE364a Notes")

    def test_a_name_made_only_of_noise_keeps_its_words(self) -> None:
        """Better an ugly name than an empty one."""
        self.assertEqual(readable_title("1080p_final.mp4"), "1080p Final")

    def test_a_bare_identifier_is_left_alone(self) -> None:
        """Nothing here invents a title: 1706.03762v7 has no words to find, so
        it is returned as it stands rather than rearranged into a different
        identifier."""
        self.assertEqual(readable_title("1706.03762v7.pdf"), "1706.03762v7")

    def test_a_download_sites_domain_is_not_part_of_the_title(self) -> None:
        """Real names from this library. The site, the ISBNs, and the
        compress/pdf/free tail all describe the download, not the book."""
        self.assertEqual(
            readable_title(
                "pdfcoffee.com_system-design-interview-an-insiders-guide-"
                "volume-2-1736049119-9781736049112-compress-pdf-free"
            ),
            "System Design Interview an Insiders Guide Volume 2",
        )

    def test_a_second_download_marker_is_dropped_and_acronyms_restored(self) -> None:
        self.assertEqual(
            readable_title("dokumen.pub_generative-ai-system-design-interview-1 (1)"),
            "Generative AI System Design Interview 1",
        )

    def test_a_dotted_name_does_not_lose_everything_after_its_first_dot(self) -> None:
        """`Path.stem` cuts at the last dot, which left "pdfcoffee"."""
        self.assertEqual(
            readable_title("machine.learning.notes.pdf"), "Machine Learning Notes"
        )

    def test_a_run_of_underscores_is_the_colon_a_filesystem_could_not_hold(
        self,
    ) -> None:
        self.assertEqual(
            readable_title(
                "12_Dropout___A_Simple_Way_to_Prevent_Neural_Networks_from_Overfitting"
            ),
            "Dropout: A Simple Way to Prevent Neural Networks from Overfitting",
        )

    def test_a_collection_index_is_not_part_of_the_name(self) -> None:
        self.assertEqual(
            readable_title("26_Kolmogorov_Complexity_and_Algorithmic_Randomness"),
            "Kolmogorov Complexity and Algorithmic Randomness",
        )

    def test_an_arxiv_id_prefix_is_dropped(self) -> None:
        self.assertEqual(
            readable_title("[1409.2329] Recurrent Neural Network Regularization"),
            "Recurrent Neural Network Regularization",
        )

    def test_a_path_is_reduced_to_its_own_name(self) -> None:
        self.assertEqual(
            readable_title("/Users/x/Downloads/quantum_notes.pdf"), "Quantum Notes"
        )


class ResolveTitleTests(unittest.TestCase):
    def test_an_authored_metadata_title_wins(self) -> None:
        self.assertEqual(
            resolve_title(embedded="Fluent Python", filename="fp2e-final.pdf"),
            "Fluent Python",
        )

    def test_a_placeholder_metadata_title_is_refused(self) -> None:
        self.assertEqual(
            resolve_title(embedded="untitled", filename="convex-optimization.pdf"),
            "Convex Optimization",
        )

    def test_a_word_export_name_is_refused(self) -> None:
        self.assertEqual(
            resolve_title(
                embedded="Microsoft Word - chapter3.docx",
                filename="graph-theory-chapter-3.pdf",
            ),
            "Graph Theory Chapter 3",
        )

    def test_metadata_that_is_only_the_filename_is_refused(self) -> None:
        self.assertEqual(
            resolve_title(embedded="1706.03762v7.pdf", filename="1706.03762v7.pdf"),
            "1706.03762v7",
        )

    def test_the_first_page_title_is_used_when_metadata_has_none(self) -> None:
        """A paper's real name usually exists only on its own first page."""
        self.assertEqual(
            resolve_title(
                embedded=None,
                from_content="Attention Is All You Need",
                filename="1706.03762v7.pdf",
            ),
            "Attention Is All You Need",
        )

    def test_content_is_only_consulted_after_metadata(self) -> None:
        self.assertEqual(
            resolve_title(
                embedded="Fluent Python",
                from_content="Chapter 1. The Python Data Model",
                filename="fp.pdf",
            ),
            "Fluent Python",
        )

    def test_whitespace_in_an_extracted_title_is_collapsed(self) -> None:
        self.assertEqual(
            resolve_title(
                from_content="Attention Is\n  All You Need", filename="x.pdf"
            ),
            "Attention Is All You Need",
        )


class MachineGeneratedTests(unittest.TestCase):
    def test_a_stored_filename_is_replaceable(self) -> None:
        self.assertTrue(
            looks_machine_generated(
                "cme295-lecture1-h264.mp4", "cme295-lecture1-h264.mp4"
            )
        )

    def test_a_stored_stem_is_replaceable(self) -> None:
        self.assertTrue(
            looks_machine_generated("cme295-lecture1", "cme295-lecture1.mp4")
        )

    def test_the_youtube_placeholder_is_replaceable(self) -> None:
        self.assertTrue(
            looks_machine_generated("YouTube video dQw4w9WgXcQ", "irrelevant.mp4")
        )

    def test_a_name_a_reader_typed_is_never_replaced(self) -> None:
        """A rename is a decision; re-deriving over it undoes the reader's work
        every time the heuristic changes."""
        self.assertFalse(
            looks_machine_generated(
                "CME 295 — Transformers, Lecture 1", "cme295-lecture1-h264.mp4"
            )
        )

    def test_an_extracted_title_is_not_replaced_again(self) -> None:
        self.assertFalse(
            looks_machine_generated("Attention Is All You Need", "1706.03762v7.pdf")
        )


if __name__ == "__main__":
    unittest.main()
