import unittest
from pathlib import Path

from evals.interview_dataset import coverage_summary, load_interview_dataset

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation" / "interview_answer_seed.json"


class InterviewDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_interview_dataset(DATASET_PATH)
        cls.summary = coverage_summary(cls.dataset)

    def test_seed_dataset_has_the_planned_size_and_review_label(self):
        self.assertEqual(self.summary["case_count"], 30)
        self.assertEqual(self.dataset.review.status, "pending_human_review")

    def test_dataset_spans_all_books_depths_and_primary_archetypes(self):
        self.assertEqual(set(self.summary["books"]), {"islp", "sdi", "aie", "dmls"})
        self.assertEqual(
            set(self.summary["expected_depths"]), {"quick", "interview", "deep"}
        )
        self.assertTrue(
            {
                "concept_explanation",
                "system_design",
                "chapter_review",
            }.issubset(self.summary["archetypes"])
        )

    def test_dataset_exercises_follow_ups_overrides_and_abstention(self):
        self.assertGreaterEqual(self.summary["follow_up_cases"], 4)
        self.assertGreaterEqual(self.summary["explicit_depth_overrides"], 4)
        self.assertGreaterEqual(self.summary["unanswerable_count"], 3)

    def test_every_answerable_case_has_grounding_anchors_and_failure_guards(self):
        for case in self.dataset.cases:
            self.assertTrue(case.must_avoid, case.id)
            if case.answerable:
                self.assertTrue(case.candidate_evidence, case.id)
                self.assertGreaterEqual(len(case.must_cover), 2, case.id)
            else:
                self.assertFalse(case.candidate_evidence, case.id)


if __name__ == "__main__":
    unittest.main()
