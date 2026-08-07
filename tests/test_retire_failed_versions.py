"""What makes a failed ingestion version safe to remove.

Keeping a failed version is how a failure stays explainable, so removing one
is only defensible when nothing can still refer to it. These tests pin the
eligibility rules rather than the printing: every one of them is a way this
could delete something a reader would notice was gone.
"""

import unittest
from uuid import uuid4

from psycopg.types.json import Jsonb

from scripts.retire_failed_versions import ELIGIBLE
from storage.database import connection, resolve_database_url
from video.repository import create_youtube_video


class EligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@test.local"),
            )
        self.addCleanup(self._discard)
        with connection(self.database_url) as database:
            created = create_youtube_video(
                database,
                owner_id=self.owner,
                idempotency_key=uuid4(),
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            )
        self.video_id = created.video_id
        self.source_id = created.source_id

    def _discard(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = %s", (self.owner,)
            )

    def version(
        self,
        database,
        *,
        number: int,
        status: str = "failed",
        completed: str = "30 days",
    ):
        """One settled version. The schema ties `published_at` and
        `quality_gates_json` to the published statuses, so those travel with
        the status rather than being set independently."""

        published = status in {"ready", "degraded"}
        version_id = uuid4()
        database.execute(
            f"""
            insert into video.ingestion_versions (
                id, owner_id, video_id, video_source_id, version_number,
                status, config_json, config_hash, quality_gates_json,
                cost_cap_usd, started_at, completed_at, published_at
            ) values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, 1.0,
                now() - interval '{completed}' - interval '1 hour',
                now() - interval '{completed}',
                {"now() - interval '" + completed + "'" if published else "null"}
            )
            """,
            (
                version_id,
                self.owner,
                self.video_id,
                self.source_id,
                number,
                status,
                Jsonb({}),
                "b" * 64,
                Jsonb({"transcript": "pass"} if published else {}),
            ),
        )
        return version_id

    def eligible(self, database, *, days: int = 7) -> set:
        return {
            row["id"]
            for row in database.execute(ELIGIBLE, {"days": days}).fetchall()
            if row["video_id"] == self.video_id
        }

    def test_a_settled_failure_with_nothing_behind_it_is_eligible(self) -> None:
        with connection(self.database_url) as database:
            version = self.version(database, number=2)
            self.assertIn(version, self.eligible(database))

    def test_a_recent_failure_is_left_alone(self) -> None:
        """The row is how a failure stays readable while anyone still cares."""

        with connection(self.database_url) as database:
            version = self.version(database, number=2, completed="1 hour")
            self.assertNotIn(version, self.eligible(database))

    def test_a_degraded_version_that_published_is_never_eligible(self) -> None:
        """The production lecture has two of these. They published, they hold
        evidence, and only the current one is in use — but a superseded
        publication is not a failure and is not this script's to remove."""

        with connection(self.database_url) as database:
            version = self.version(database, number=2, status="degraded")
            self.assertNotIn(version, self.eligible(database))

    def test_a_version_something_was_answered_from_is_never_eligible(self) -> None:
        with connection(self.database_url) as database:
            version = self.version(database, number=2)
            source = database.execute(
                """
                insert into video.transcript_sources (
                    owner_id, video_id, video_source_id, source_kind, language,
                    provider, storage_backend, storage_key, content_hash,
                    timing_kind
                ) values (
                    %s, %s, %s, 'uploaded_caption', 'en', 'test', 'filesystem',
                    %s, %s, 'cue'
                ) returning id
                """,
                (
                    self.owner,
                    self.video_id,
                    self.source_id,
                    f"{self.owner}/canonical/transcripts/sha256/aa/bb/aabb.vtt",
                    "d" * 64,
                ),
            ).fetchone()["id"]
            segment = database.execute(
                """
                insert into video.transcript_segments (
                    owner_id, video_id, transcript_source_id, cue_index,
                    start_ms, end_ms, text
                ) values (%s, %s, %s, 0, 1000, 2000, 'said something')
                returning id
                """,
                (self.owner, self.video_id, source),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.evidence_units (
                    id, owner_id, video_id, ingestion_version_id, modality,
                    transcript_segment_id, retrieval_text, start_ms, end_ms,
                    content_hash
                ) values (
                    %s, %s, %s, %s, 'transcript', %s, 'said something',
                    1000, 2000, %s
                )
                """,
                (
                    "e" * 64,
                    self.owner,
                    self.video_id,
                    version,
                    segment,
                    "c" * 64,
                ),
            )
            self.assertNotIn(version, self.eligible(database))

    def test_a_version_with_a_running_job_is_never_eligible(self) -> None:
        """One active job per video is a unique index, so the running job is
        the one the video already has, retargeted."""

        with connection(self.database_url) as database:
            version = self.version(database, number=2)
            database.execute(
                """
                update video.ingestion_jobs
                set target_version_id = %s, status = 'running'
                where owner_id = %s and video_id = %s
                """,
                (version, self.owner, self.video_id),
            )
            self.assertNotIn(version, self.eligible(database))

    def test_the_database_already_forbids_the_two_worst_states(self) -> None:
        """Two eligibility rules guard states the schema will not produce.

        A failed version cannot be a video's current version, and a
        conversation turn cannot cite one. Both are enforced by triggers, so
        the checks in the query are defence in depth — worth keeping, because
        a query that reads as if it were the only guard invites someone to
        drop it. This test is what says they are not the only guard.
        """

        with connection(self.database_url) as database:
            version = self.version(database, number=2)
            with self.assertRaises(Exception):
                with database.transaction():
                    database.execute(
                        """
                        update video.videos
                        set current_ingestion_version_id = %s where id = %s
                        """,
                        (version, self.video_id),
                    )
            conversation = database.execute(
                """
                insert into video.conversations (owner_id, video_id, title)
                values (%s, %s, 'a chat') returning id
                """,
                (self.owner, self.video_id),
            ).fetchone()["id"]
            with self.assertRaises(Exception):
                with database.transaction():
                    database.execute(
                        """
                        insert into video.conversation_turns (
                            owner_id, video_id, conversation_id,
                            ingestion_version_id, turn_index, question, answer
                        ) values (%s, %s, %s, %s, 1, 'q', 'a')
                        """,
                        (self.owner, self.video_id, conversation, version),
                    )

    def test_a_ready_version_is_never_eligible(self) -> None:
        with connection(self.database_url) as database:
            version = self.version(database, number=2, status="ready")
            self.assertNotIn(version, self.eligible(database))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
