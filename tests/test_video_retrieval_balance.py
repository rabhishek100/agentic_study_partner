"""What reaches the answer, and how much of the lecture each piece carries.

Two defects found by the gold set, both invisible to a test that only asked
whether retrieval returned something.

Ranking alone gave frames 133 of 184 evidence slots across the set, because a
frame's description runs to about 1,600 characters and a caption cue to about
30 — so the frame wins on lexical and vector scores almost regardless of the
question. And the one transcript slot that survived held six words, which is
not evidence a claim can rest on.
"""

from itertools import combinations
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.answers import VideoAnswerDependencies, retrieve_turn_evidence
from video.retrieval import (
    MODALITY_SHARE,
    VideoEvidence,
    TRANSCRIPT_PASSAGE_CHARACTERS,
    _balanced_direct,
    _expand_transcript,
    _kind,
    _per_modality,
)


class Candidate:
    """The shape `_balanced_direct` reads, without a database behind it."""

    def __init__(self, identity: str, modality: str) -> None:
        self.id = identity
        self.modality = modality

    @property
    def is_visual(self) -> bool:
        return self.modality in {"visual_frame", "visual_event"}

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return f"{self.id}:{self.modality}"


def caption_file() -> str:
    """Two minutes of lecture, cut the way a caption file is cut."""

    lines = ["WEBVTT", ""]
    for index in range(40):
        start, end = 20 + index * 3, 22 + index * 3
        lines += [
            f"00:{start // 60:02d}:{start % 60:02d}.000 --> "
            f"00:{end // 60:02d}:{end % 60:02d}.500",
            f"attention lets the decoder look back at the encoder, step {index}",
            "",
        ]
    return "\n".join(lines)


def ranked(*modalities: str) -> list[Candidate]:
    return [
        Candidate(f"c{index}", modality)
        for index, modality in enumerate(modalities)
    ]


class ModalityBudgetTests(unittest.TestCase):
    def test_frames_no_longer_take_every_slot_they_outrank(self) -> None:
        """The production shape: frames sweep the fused ranking."""

        candidates = ranked(*(["visual_frame"] * 20 + ["transcript"] * 20))
        selected = _balanced_direct(candidates, limit=8)
        counts = {
            kind: sum(1 for item in selected if _kind(item) == kind)
            for kind in ("transcript", "visual")
        }

        self.assertEqual(len(selected), 8)
        self.assertGreaterEqual(counts["transcript"], 4)
        self.assertGreaterEqual(counts["visual"], 3)

    def test_a_share_nothing_can_fill_goes_back_to_the_ranking(self) -> None:
        """A lecture with nothing but frames must still fill the answer.

        The budget is a floor against starvation, not a quota that leaves
        slots empty when only one modality has anything to say.
        """

        selected = _balanced_direct(ranked(*(["visual_frame"] * 12)), limit=8)
        self.assertEqual(len(selected), 8)

    def test_naming_the_deck_puts_the_deck_first(self) -> None:
        candidates = ranked(
            *(["visual_frame"] * 10 + ["transcript"] * 10 + ["resource_page"] * 10)
        )
        selected = _balanced_direct(candidates, limit=8, document_floor=3)
        pages = sum(1 for item in selected if item.modality == "resource_page")
        self.assertGreaterEqual(pages, 3)

    def test_the_budget_decides_presence_not_reading_order(self) -> None:
        # The model should still meet the strongest evidence first; the budget
        # only changes which items are in the set.
        candidates = ranked("visual_frame", "transcript", "visual_frame")
        selected = _balanced_direct(candidates, limit=3)
        self.assertEqual([item.id for item in selected], ["c0", "c1", "c2"])

    def test_the_shares_add_up_to_the_whole_evidence_set(self) -> None:
        self.assertAlmostEqual(sum(MODALITY_SHARE.values()), 1.0)


class ShortlistTests(unittest.TestCase):
    """Which candidates the budget gets to choose between.

    A shortlist cut globally hands itself to whichever modality embeds best,
    which for this corpus is always the frames: 258 paragraph-length
    descriptions against 2,436 caption fragments and 135 short deck pages. The
    budget downstream then has a share to fill and nothing to fill it from —
    measured on the gold set as questions naming the deck coming back with no
    deck page in them at all.
    """

    def test_each_modality_keeps_its_own_best(self) -> None:
        candidates = ranked(
            *(["visual_frame"] * 30 + ["resource_page"] * 5 + ["transcript"] * 5)
        )
        kept = _per_modality(candidates, limit=4)
        counts = {
            kind: sum(1 for item in kept if _kind(item) == kind)
            for kind in ("visual", "resource_page", "transcript")
        }
        self.assertEqual(counts, {"visual": 4, "resource_page": 4, "transcript": 4})

    def test_ranking_inside_a_modality_is_untouched(self) -> None:
        candidates = ranked("visual_frame", "transcript", "visual_frame", "transcript")
        kept = _per_modality(candidates, limit=1)
        self.assertEqual([item.id for item in kept], ["c0", "c1"])

    def test_a_modality_with_less_than_its_share_keeps_what_it_has(self) -> None:
        kept = _per_modality(ranked("transcript", "visual_frame"), limit=10)
        self.assertEqual(len(kept), 2)


class PassageMergeTests(unittest.TestCase):
    """Merging happens in time order, not in the order cues arrived."""

    def expand(self, cues):
        rows = [
            {"start_ms": t, "end_ms": t + 1000, "retrieval_text": f"cue at {t}"}
            for t in range(0, 120_000, 1000)
        ]
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = rows
        items = [
            VideoEvidence(
                id=f"e{index}", modality="transcript", text="x",
                start_ms=start, end_ms=start + 1000, page_number=None,
                transcript_segment_id=None, frame_id=None, visual_event_id=None,
                resource_page_id=None, score=1.0, retrieval_method="fts",
            )
            for index, start in enumerate(cues)
        ]
        return _expand_transcript(
            connection, items, owner="o", video="v", version_id="ver",
            candidates=[], limit=8,
        )

    def test_a_cue_bridging_two_passages_joins_them_all(self) -> None:
        """The third cue overlaps both of the first two.

        Merging it into whichever window it met first leaves those two
        overlapping each other — the duplication the merge exists to prevent,
        reappearing only when the cues arrive in an awkward order.
        """

        spans = [(item.start_ms, item.end_ms) for item in self.expand([0, 72_000, 40_000])]
        self.assertEqual(len(spans), 1)
        for first, second in combinations(spans, 2):
            self.assertFalse(first[0] < second[1] and first[1] > second[0])

    def test_cues_far_apart_stay_separate_passages(self) -> None:
        spans = [(item.start_ms, item.end_ms) for item in self.expand([0, 100_000])]
        self.assertEqual(len(spans), 2)


class TranscriptPassageTests(unittest.TestCase):
    """Small-to-big: match on the cue, return the passage around it."""

    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-balance.test"),
            )
            self.video = publish_video_with_evidence(
                database, owner_id=self.owner, transcript_vtt=caption_file()
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def retrieve(self, query: str):
        with connection(self.database_url, readonly=True) as database:
            return retrieve_turn_evidence(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                query=query,
                dependencies=VideoAnswerDependencies(),
            ).evidence

    def test_a_retrieved_cue_arrives_as_a_passage_not_a_fragment(self) -> None:
        evidence = self.retrieve("attention")
        spoken = [item for item in evidence if item.modality == "transcript"]
        self.assertTrue(spoken)
        for item in spoken:
            with self.subTest(rank=item.rank):
                self.assertLessEqual(
                    len(item.excerpt), TRANSCRIPT_PASSAGE_CHARACTERS + 200
                )
                # The span a passage reports is the span it actually carries,
                # because that is what a citation sends the reader to.
                self.assertIsNotNone(item.start_ms)
                self.assertIsNotNone(item.end_ms)
                self.assertGreaterEqual(item.end_ms, item.start_ms)

    def test_two_cues_from_one_moment_do_not_return_the_same_words_twice(
        self,
    ) -> None:
        evidence = self.retrieve("attention encoder decoder tokens step")
        spoken = [item for item in evidence if item.modality == "transcript"]
        spans = [(item.start_ms, item.end_ms) for item in spoken]
        for index, (start, end) in enumerate(spans):
            for other_start, other_end in spans[index + 1 :]:
                with self.subTest(span=(start, end)):
                    self.assertFalse(
                        start < other_end and end > other_start,
                        "overlapping passages waste a slot on repeated words",
                    )

    def test_a_lecture_with_no_transcript_still_answers(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from video.evidence_units where owner_id = %s "
                "and modality = 'transcript'",
                (self.owner,),
            )
        evidence = self.retrieve("attention")
        self.assertTrue(evidence)
        self.assertFalse(
            [item for item in evidence if item.modality == "transcript"]
        )


if __name__ == "__main__":
    unittest.main()
