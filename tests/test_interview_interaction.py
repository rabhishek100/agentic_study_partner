"""Transcript corpus and adaptive-interaction evaluation contracts."""

from pathlib import Path

from evals.interview_interaction import load_interview_interaction_dataset
from interviews.question_generation import validate_question_focus


ROOT = Path(__file__).resolve().parents[1]
DATASET = load_interview_interaction_dataset(
    ROOT / "evaluation" / "interview_transcript_eval.json"
)


def test_transcript_corpus_is_diverse_and_pre_split() -> None:
    channels = {source.channel for source in DATASET.sources}
    roles = {case.role for case in DATASET.cases}

    assert len(channels) >= 5
    assert {"development", "held_out"} == {
        source.split for source in DATASET.sources
    }
    assert {
        "software engineer",
        "AI engineer",
        "machine learning engineer",
        "senior software engineer",
        "senior machine learning engineer",
    } <= roles


def test_cases_never_cross_the_transcript_split() -> None:
    sources = {source.id: source for source in DATASET.sources}

    for case in DATASET.cases:
        assert all(sources[source_id].split == case.split for source_id in case.source_ids)


def test_dataset_stores_observations_instead_of_full_transcripts() -> None:
    for source in DATASET.sources:
        assert source.url == f"https://www.youtube.com/watch?v={source.video_id}"
        assert all(
            len(observation.behavior.split()) <= 45
            for observation in source.observations
        )


def test_primary_questions_are_focused_and_source_independent() -> None:
    for case in DATASET.cases:
        validate_question_focus(case.primary_question)
