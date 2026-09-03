"""Superseded ingestion versions are reclaimed, except the ones still spoken for.

Re-ingesting leaves the previous version whole, which is what makes a failed
rebuild survivable and what makes the space leak. These pin the three reasons
a superseded version is not garbage.
"""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.version_retention import (
    dry_run_requested,
    prune_superseded_versions,
)


class VersionRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@test.local"),
            )
        self.addCleanup(self._discard)

    def _discard(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def supersede(self, database, published, *, number: int = 2):
        """Add a newer published version, leaving the old one behind."""

        version = uuid4()
        database.execute(
            """
            insert into video.ingestion_versions
                (id, owner_id, video_id, video_source_id, version_number,
                 status, config_json, config_hash, quality_gates_json,
                 published_at, completed_at)
            select %s, owner_id, video_id, video_source_id, %s, 'ready',
                   config_json, config_hash,
                   coalesce(nullif(quality_gates_json, '{}'::jsonb),
                            '{"gates": {}}'::jsonb),
                   now(), now()
            from video.ingestion_versions where id = %s
            """,
            (version, number, published.version_id),
        )
        database.execute(
            "update video.videos set current_ingestion_version_id = %s where id = %s",
            (version, published.video_id),
        )
        # The ingestion that produced the old version has finished; leaving its
        # job running would (correctly) protect the version from any prune.
        database.execute(
            "update video.ingestion_jobs set status = 'ready', completed_at = now() "
            "where target_version_id = %s",
            (published.version_id,),
        )
        return version

    def prune(self, database, **kwargs):
        return prune_superseded_versions(database, dry_run=False, **kwargs)

    def test_a_superseded_version_is_reclaimed(self) -> None:
        with connection(self.database_url) as database:
            published = publish_video_with_evidence(database, owner_id=self.owner)
            self.supersede(database, published)

            summary = self.prune(database, keep=0)

            remaining = database.execute(
                "select count(*) as n from video.ingestion_versions where id = %s",
                (published.version_id,),
            ).fetchone()["n"]

        self.assertEqual(remaining, 0)
        self.assertGreaterEqual(summary.versions_removed, 1)

    def test_the_current_version_is_never_touched(self) -> None:
        with connection(self.database_url) as database:
            published = publish_video_with_evidence(database, owner_id=self.owner)

            summary = self.prune(database, keep=0)

            alive = database.execute(
                "select count(*) as n from video.ingestion_versions where id = %s",
                (published.version_id,),
            ).fetchone()["n"]

        self.assertEqual(alive, 1)
        self.assertEqual(summary.versions_removed, 0)

    def test_a_version_an_answer_cited_is_kept(self) -> None:
        """A citation whose version has been deleted cannot be explained.

        `conversation_turns` records where an answer came from and deliberately
        does not cascade. Reclaiming space is not worth making a past answer
        unaccountable.
        """

        with connection(self.database_url) as database:
            published = publish_video_with_evidence(database, owner_id=self.owner)
            conversation = database.execute(
                """
                insert into video.conversations (owner_id, video_id, title)
                values (%s, %s, 'grounded') returning id
                """,
                (self.owner, published.video_id),
            ).fetchone()["id"]
            database.execute(
                """
                insert into video.conversation_turns
                    (owner_id, conversation_id, video_id, ingestion_version_id,
                     turn_index, status, question, rewritten_query, answer,
                     result_json, completed_at)
                values (%s, %s, %s, %s, 0, 'complete', 'q', 'q', 'a',
                        '{"outcome": "answer"}'::jsonb, now())
                """,
                (self.owner, conversation, published.video_id,
                 published.version_id),
            )
            self.supersede(database, published)

            summary = self.prune(database, keep=0)

            alive = database.execute(
                "select count(*) as n from video.ingestion_versions where id = %s",
                (published.version_id,),
            ).fetchone()["n"]

        self.assertEqual(alive, 1)
        self.assertEqual(summary.versions_removed, 0)
        self.assertGreaterEqual(summary.versions_kept_for_citations, 1)

    def test_a_version_a_job_is_still_working_on_is_kept(self) -> None:
        """An in-flight version looks superseded from outside and is not."""

        with connection(self.database_url) as database:
            published = publish_video_with_evidence(database, owner_id=self.owner)
            self.supersede(database, published)
            # A rebuild is under way against the older version again.
            database.execute(
                "update video.ingestion_jobs set status = 'running', "
                "completed_at = null, target_version_id = %s where id = %s",
                (published.version_id, published.job_id),
            )

            summary = self.prune(database, keep=0)

            alive = database.execute(
                "select count(*) as n from video.ingestion_versions where id = %s",
                (published.version_id,),
            ).fetchone()["n"]

        self.assertEqual(alive, 1)
        self.assertEqual(summary.versions_removed, 0)

    def test_one_previous_version_is_kept_for_rollback_by_default(self) -> None:
        with connection(self.database_url) as database:
            published = publish_video_with_evidence(database, owner_id=self.owner)
            self.supersede(database, published)

            summary = self.prune(database)  # keep defaults to 1

            alive = database.execute(
                "select count(*) as n from video.ingestion_versions where id = %s",
                (published.version_id,),
            ).fetchone()["n"]

        self.assertEqual(alive, 1)
        self.assertEqual(summary.versions_removed, 0)
        self.assertEqual(summary.versions_kept_for_rollback, 1)

    def test_reporting_is_the_default_and_removes_nothing(self) -> None:
        with connection(self.database_url) as database:
            published = publish_video_with_evidence(database, owner_id=self.owner)
            self.supersede(database, published)

            summary = prune_superseded_versions(database, keep=0)

            alive = database.execute(
                "select count(*) as n from video.ingestion_versions where id = %s",
                (published.version_id,),
            ).fetchone()["n"]

        self.assertTrue(summary.dry_run)
        self.assertEqual(alive, 1)
        self.assertGreaterEqual(summary.versions_removed, 1)

    def test_deleting_is_the_thing_an_operator_has_to_ask_for(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(dry_run_requested())
        for value in ("0", "false", "no"):
            with patch.dict(
                os.environ, {"VIDEO_VERSION_PRUNE_DRY_RUN": value}, clear=False
            ):
                self.assertFalse(dry_run_requested(), value)


if __name__ == "__main__":
    unittest.main()
