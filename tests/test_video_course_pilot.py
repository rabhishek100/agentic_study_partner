from __future__ import annotations

from pathlib import Path

import numpy as np

from experiments.video_course.artifacts import CostLedger, CostLimitExceeded
from experiments.video_course.frames import difference_hash, hamming_distance, jaccard
from experiments.video_course.models import Chapter, EvidenceUnit
from experiments.video_course.pipeline import chapters_from_metadata
from experiments.video_course.retrieval import MultimodalIndex
from experiments.video_course.transcript import parse_vtt


def test_youtube_rolling_captions_become_stable_segments(tmp_path: Path) -> None:
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        """WEBVTT

00:00:00.000 --> 00:00:02.000
hello

00:00:02.000 --> 00:00:04.000
hello transformers

00:00:04.000 --> 00:00:06.000
transformers use attention
""",
        encoding="utf-8",
    )
    chapters = [Chapter(0, "Start", 0, 10_000)]
    segments = parse_vtt(vtt, chapters=chapters, target_window_ms=5_000)

    assert len(segments) == 1
    assert segments[0].text == "hello transformers use attention"
    assert segments[0].chapter_index == 0


def test_visual_deduplication_signals_are_deterministic() -> None:
    same = np.zeros((32, 32), dtype=np.uint8)
    changed = same.copy()
    changed[:, 16:] = 255

    assert hamming_distance(difference_hash(same), difference_hash(same)) == 0
    assert hamming_distance(difference_hash(same), difference_hash(changed)) > 0
    assert jaccard("self attention diagram", "diagram of self attention") == 0.75


def test_bm25_prefers_matching_visual_evidence() -> None:
    evidence = [
        EvidenceUnit(
            "visual-1",
            "visual",
            "video",
            0,
            1_000,
            2_000,
            None,
            "diagram shows query key value attention arrows",
        ),
        EvidenceUnit(
            "transcript-1",
            "transcript",
            "captions",
            0,
            1_000,
            2_000,
            None,
            "welcome and course logistics",
        ),
    ]

    result = MultimodalIndex(evidence).search("query key value diagram", limit=2)

    assert result[0].evidence_id == "visual-1"
    assert "bm25" in result[0].retrieval_methods


def test_cost_ledger_blocks_request_before_cap_is_crossed(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "cost.json", maximum_usd=0.05)
    ledger.record({"cost_usd": 0.04})

    try:
        ledger.reserve(0.02, operation="test")
    except CostLimitExceeded as error:
        assert "only $0.0100 remains" in str(error)
    else:
        raise AssertionError("expected CostLimitExceeded")


def test_chapters_use_official_metadata_boundaries() -> None:
    chapters = chapters_from_metadata(
        {
            "chapters": [
                {"title": "Intro", "start_time": 0, "end_time": 10.5},
                {"title": "Attention", "start_time": 10.5, "end_time": 20},
            ]
        },
        20_000,
    )

    assert chapters == [
        Chapter(0, "Intro", 0, 10_500),
        Chapter(1, "Attention", 10_500, 20_000),
    ]
