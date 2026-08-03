"""Owner-scoped persistence of canonical video transcripts."""

import unittest
from uuid import uuid4

from psycopg.errors import NumericValueOutOfRange

from storage.database import connection, resolve_database_url
from video.repository import create_youtube_video
from video.transcript_store import (
    TranscriptSourceConflictError,
    TranscriptSourceNotFoundError,
    persist_transcript,
)
from video.transcripts import TranscriptCue, parse_webvtt


CAPTIONS = """WEBVTT

00:00.000 --> 00:04.000
attention is all you need

00:03.000 --> 00:08.000
transformers use attention
"""


class VideoTranscriptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner, self.other_owner = uuid4(), uuid4()
        with connection(self.database_url) as database:
            for owner in (self.owner, self.other_owner):
                database.execute(
                    "insert into auth.users (id, email) values (%s, %s)",
                    (owner, f"{owner}@video-transcript-store.test"),
                )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner, self.other_owner],),
            )

    def create_source(self, database, *, video_id: str = "abcdefghijk"):
        created = create_youtube_video(
            database,
            owner_id=self.owner,
            idempotency_key=uuid4(),
            url=f"https://youtu.be/{video_id}",
            title="Lecture",
        )
        database.execute(
            "update video.videos set duration_ms = 10000 where id = %s",
            (created.video_id,),
        )
        return created

    def persist(self, database, created, **overrides):
        values = {
            "owner_id": self.owner,
            "video_id": created.video_id,
            "video_source_id": created.source_id,
            "source_kind": "youtube_caption",
            "language": "en",
            "provider": "youtube",
            "storage_backend": "filesystem",
            "storage_key": f"{self.owner}/transcripts/{created.source_id}.vtt",
            "content_hash": "a" * 64,
            "cues": parse_webvtt(CAPTIONS),
            "provenance": {"track": "English"},
        }
        values.update(overrides)
        return persist_transcript(database, **values)

    def test_persists_source_cues_raw_text_and_union_coverage_atomically(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_source(database)
            persisted = self.persist(database, created)
            source = database.execute(
                """
                select language, provider, storage_key, timing_kind,
                       coverage_ratio, detected_speech, provenance_json
                from video.transcript_sources where id = %s
                """,
                (persisted.id,),
            ).fetchone()
            segments = database.execute(
                """
                select cue_index, start_ms, end_ms, text, raw_json
                from video.transcript_segments
                where transcript_source_id = %s order by cue_index
                """,
                (persisted.id,),
            ).fetchall()

        self.assertTrue(persisted.created)
        self.assertEqual(persisted.segment_count, 2)
        self.assertEqual(persisted.coverage_ratio, 0.8)
        self.assertEqual(source["coverage_ratio"], 0.8)
        self.assertEqual(source["timing_kind"], "cue")
        self.assertTrue(source["detected_speech"])
        self.assertEqual(source["provenance_json"], {"track": "English"})
        self.assertTrue(source["storage_key"].startswith(f"{self.owner}/"))
        self.assertEqual([row["cue_index"] for row in segments], [0, 1])
        self.assertEqual(segments[0]["text"], "attention is all you need")
        self.assertEqual(
            segments[0]["raw_json"], {"raw_text": "attention is all you need"}
        )

    def test_source_and_hash_replay_does_not_duplicate_rows(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_source(database)
            first = self.persist(database, created)
            replay = self.persist(database, created)
            counts = database.execute(
                """
                select
                    (select count(*) from video.transcript_sources
                     where video_source_id = %s) as sources,
                    (select count(*) from video.transcript_segments
                     where transcript_source_id = %s) as segments
                """,
                (created.source_id, first.id),
            ).fetchone()

        self.assertFalse(replay.created)
        self.assertEqual(replay.id, first.id)
        self.assertEqual(replay.segment_count, first.segment_count)
        self.assertEqual(dict(counts), {"sources": 1, "segments": 2})

    def test_foreign_owner_and_mismatched_video_cannot_persist(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_source(database)
            another = self.create_source(database, video_id="lmnopqrstuv")
            with self.assertRaises(TranscriptSourceNotFoundError):
                self.persist(
                    database,
                    created,
                    owner_id=self.other_owner,
                    storage_key=(
                        f"{self.other_owner}/transcripts/{created.source_id}.vtt"
                    ),
                )
            with self.assertRaises(TranscriptSourceNotFoundError):
                self.persist(database, created, video_id=another.video_id)
            count = database.execute(
                "select count(*) as count from video.transcript_sources"
            ).fetchone()["count"]

        self.assertEqual(count, 0)

    def test_invalid_cues_roll_back_without_partial_source(self) -> None:
        duplicate_cues = [
            TranscriptCue(0, 0, 1_000, "first", "first"),
            TranscriptCue(0, 1_000, 2_000, "second", "second"),
        ]
        with connection(self.database_url) as database:
            created = self.create_source(database)
            with self.assertRaises(ValueError):
                self.persist(database, created, cues=duplicate_cues)
            count = database.execute(
                "select count(*) as count from video.transcript_sources"
            ).fetchone()["count"]

        self.assertEqual(count, 0)

    def test_segment_database_failure_rolls_back_new_transcript_source(self) -> None:
        overflowing_cue = TranscriptCue(
            0,
            0,
            10**30,
            "timestamp outside bigint range",
            "timestamp outside bigint range",
        )
        with connection(self.database_url) as database:
            created = self.create_source(database)
            with self.assertRaises(NumericValueOutOfRange):
                self.persist(database, created, cues=[overflowing_cue])
            count = database.execute(
                "select count(*) as count from video.transcript_sources"
            ).fetchone()["count"]

        self.assertEqual(count, 0)

    def test_ready_source_is_accepted_but_other_states_are_rejected(self) -> None:
        with connection(self.database_url) as database:
            ready = self.create_source(database)
            database.execute(
                """
                update video.video_sources
                set status = 'ready', storage_backend = 'filesystem',
                    storage_key = %s, content_hash = %s, size_bytes = 100,
                    media_type = 'video/mp4', acquired_at = now()
                where id = %s
                """,
                (f"{self.owner}/videos/ready.mp4", "b" * 64, ready.source_id),
            )
            persisted = self.persist(database, ready)

            acquiring = self.create_source(database, video_id="zyxwvutsrqp")
            database.execute(
                "update video.video_sources set status = 'acquiring' where id = %s",
                (acquiring.source_id,),
            )
            with self.assertRaises(TranscriptSourceConflictError):
                self.persist(database, acquiring, content_hash="c" * 64)

        self.assertTrue(persisted.created)

    def test_duration_is_required_for_meaningful_coverage(self) -> None:
        with connection(self.database_url) as database:
            created = self.create_source(database)
            database.execute(
                "update video.videos set duration_ms = null where id = %s",
                (created.video_id,),
            )
            with self.assertRaises(TranscriptSourceConflictError):
                self.persist(database, created)
            count = database.execute(
                "select count(*) as count from video.transcript_sources"
            ).fetchone()["count"]

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
