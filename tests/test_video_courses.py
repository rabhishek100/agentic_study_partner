"""Course grouping, batch creation, and multi-lecture grounding."""

import unittest
from uuid import uuid4

from psycopg.errors import CheckViolation

from storage.database import connection, resolve_database_url
from tests.video_fixtures import publish_video_with_evidence
from video.answers import VideoAnswerDependencies
from video.course_conversation import (
    execute_course_turn,
    new_course_conversation_state,
)
from video.course_conversation_store import append_turn, create_conversation
from video.course_repository import (
    attach_course_lecture,
    create_course,
    create_youtube_course,
    delete_course,
    detach_course_lecture,
    list_course_lectures,
    list_courses,
    upgrade_course_quality,
    update_course_lecture,
)
from video.repository import list_standalone_videos


class FakeReply:
    def __init__(self, content: str, cost: float = 0.0) -> None:
        self.content = content
        self.response_metadata = {"cost": cost}


class FakeAnswerModel:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests = []

    def invoke(self, messages):
        self.requests.append(messages)
        return FakeReply(self.reply, 0.004)

    def stream(self, messages):
        self.requests.append(messages)
        for piece in self.reply.split(" "):
            yield FakeReply(piece + " ")


class VideoCourseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@course.test"),
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def test_batch_course_is_idempotent_ordered_and_preserves_lectures(self) -> None:
        key = uuid4()
        inputs = [
            {"url": "https://youtu.be/abcdefghijk", "title": "Foundations"},
            {"url": "https://youtu.be/lmnopqrstuv", "title": "Attention"},
            {"url": "https://youtu.be/12345678901", "title": "Training"},
        ]
        with connection(self.database_url) as database:
            created = create_youtube_course(
                database,
                owner_id=self.owner,
                creation_key=key,
                title="Transformers",
                lectures=inputs,
            )
            replay = create_youtube_course(
                database,
                owner_id=self.owner,
                creation_key=key,
                title="Ignored on replay",
                lectures=inputs,
            )
            lectures = list_course_lectures(
                database, created.course_id, owner_id=self.owner
            )
            standalone = list_standalone_videos(database, owner_id=self.owner)
            course_cards = database.execute(
                """
                select cards_enabled from video.videos
                where owner_id = %s order by created_at
                """,
                (self.owner,),
            ).fetchall()
            update_course_lecture(
                database,
                created.course_id,
                lectures[2]["id"],
                owner_id=self.owner,
                lecture_index=0,
            )
            reordered = list_course_lectures(
                database, created.course_id, owner_id=self.owner
            )
            detached_id = reordered[0]["id"]
            self.assertTrue(
                detach_course_lecture(
                    database,
                    created.course_id,
                    detached_id,
                    owner_id=self.owner,
                )
            )
            self.assertEqual(
                [row["id"] for row in list_standalone_videos(database, owner_id=self.owner)],
                [detached_id],
            )
            self.assertTrue(delete_course(database, created.course_id, owner_id=self.owner))
            remaining = database.execute(
                "select count(*) as count from video.videos where owner_id = %s",
                (self.owner,),
            ).fetchone()["count"]

        self.assertTrue(created.created)
        self.assertFalse(replay.created)
        self.assertEqual(created.course_id, replay.course_id)
        self.assertEqual([row["lecture_index"] for row in lectures], [0, 1, 2])
        self.assertEqual(
            [row["title_override"] for row in lectures],
            ["Foundations", "Attention", "Training"],
        )
        self.assertEqual(standalone, [])
        self.assertTrue(course_cards)
        self.assertTrue(all(not row["cards_enabled"] for row in course_cards))
        self.assertEqual(reordered[0]["title_override"], "Training")
        self.assertEqual(remaining, 3)

    def test_course_budget_tracks_jobs_and_rejects_an_over_cap_charge(self) -> None:
        with connection(self.database_url) as database:
            created = create_youtube_course(
                database,
                owner_id=self.owner,
                creation_key=uuid4(),
                title="Bounded course",
                lectures=[
                    {"url": "https://youtu.be/abcdefghijk", "title": "One"},
                    {"url": "https://youtu.be/lmnopqrstuv", "title": "Two"},
                ],
                metadata={"source_kind": "youtube_playlist"},
            )
            first_job = created.lectures[0].ingestion_job_id
            self.assertIsNotNone(first_job)
            database.execute(
                """
                update video.ingestion_jobs set actual_cost_usd = 0.40
                where id = %s
                """,
                (first_job,),
            )
            course = database.execute(
                """
                select ingestion_cost_cap_usd, actual_ingestion_cost_usd
                from video.courses where id = %s
                """,
                (created.course_id,),
            ).fetchone()
            database.execute(
                """
                update video.courses set ingestion_cost_cap_usd = 0.41
                where id = %s
                """,
                (created.course_id,),
            )
            database.commit()
            with self.assertRaises(CheckViolation), database.transaction():
                database.execute(
                    """
                    update video.ingestion_jobs set actual_cost_usd = 0.42
                    where id = %s
                    """,
                    (first_job,),
                )
            after = database.execute(
                """
                select actual_ingestion_cost_usd from video.courses where id = %s
                """,
                (created.course_id,),
            ).fetchone()["actual_ingestion_cost_usd"]

        self.assertEqual(str(course["ingestion_cost_cap_usd"]), "1.350000")
        self.assertEqual(str(course["actual_ingestion_cost_usd"]), "0.400000")
        self.assertEqual(str(after), "0.400000")

    def test_full_quality_upgrade_is_atomic_and_idempotent(self) -> None:
        with connection(self.database_url) as database:
            lecture = publish_video_with_evidence(
                database,
                owner_id=self.owner,
                title="Resumable lecture",
                url="https://youtu.be/abcdefghijk",
            )
            course = create_course(
                database,
                owner_id=self.owner,
                creation_key=uuid4(),
                title="Full quality course",
            )
            attach_course_lecture(
                database,
                course.course_id,
                lecture.video_id,
                owner_id=self.owner,
            )
            database.execute(
                """
                update video.ingestion_jobs
                set status = 'ready', stage = null, lease_owner = null,
                    lease_expires_at = null, completed_at = now()
                where id = %s
                """,
                (lecture.job_id,),
            )
            key = uuid4()
            first = upgrade_course_quality(
                database,
                owner_id=self.owner,
                course_id=course.course_id,
                idempotency_key=key,
            )
            job = database.execute(
                """
                select stage, status, provenance_json, target_version_id
                from video.ingestion_jobs where id = %s
                """,
                (first.lectures[0].ingestion_job_id,),
            ).fetchone()
            current = database.execute(
                """
                select current_ingestion_version_id from video.videos where id = %s
                """,
                (lecture.video_id,),
            ).fetchone()["current_ingestion_version_id"]
            copied = database.execute(
                """
                select count(*) as count from video.frames
                where ingestion_version_id = %s
                """,
                (job["target_version_id"],),
            ).fetchone()["count"]
            replay = upgrade_course_quality(
                database,
                owner_id=self.owner,
                course_id=course.course_id,
                idempotency_key=key,
            )

        self.assertEqual(str(job["stage"]), "visual_analysis")
        self.assertEqual(str(job["status"]), "queued")
        self.assertTrue(job["provenance_json"]["analyze_all_selected_frames"])
        self.assertTrue(job["provenance_json"]["semantic_embeddings"])
        self.assertEqual(current, lecture.version_id)
        self.assertGreater(copied, 0)
        self.assertEqual(first.lectures[0].ingestion_job_id, replay.lectures[0].ingestion_job_id)
        self.assertTrue(replay.lectures[0].reused)

    def test_course_answer_keeps_mechanism_after_long_transcript_intro(self) -> None:
        tail = "Writes go to the shard leader, which replicates an ordered log to followers."
        transcript = "Paxos groups shard replication. " * 30 + tail
        model = FakeAnswerModel(tail + " [S1]")
        with connection(self.database_url) as database:
            video = publish_video_with_evidence(database, owner_id=self.owner,
                title="Sharded replication", url="https://youtu.be/abcdefghijk",
                transcript_vtt="WEBVTT\n\n00:00.000 --> 00:50.000\n" + transcript + "\n")
            excluded = publish_video_with_evidence(database, owner_id=self.owner,
                title="Excluded replication", url="https://youtu.be/lmnopqrstuv",
                transcript_vtt="WEBVTT\n\n00:00.000 --> 00:50.000\nExcluded secret mechanism.\n")
            course = create_course(database, owner_id=self.owner,
                creation_key=uuid4(), title="Replication course")
            for lecture in (video, excluded):
                attach_course_lecture(database, course.course_id, lecture.video_id, owner_id=self.owner)
            result, _, versions = execute_course_turn(database,
                "Explain Paxos shard replication and write ordering.",
                new_course_conversation_state(course_id=course.course_id),
                owner_id=self.owner, course_id=course.course_id,
                course_title="Replication course", video_ids=[video.video_id],
                dependencies=VideoAnswerDependencies(model=model))
        supplied = model.requests[0][1]["content"]
        self.assertIn(tail, supplied)
        self.assertNotIn("Excluded secret mechanism", supplied)
        self.assertEqual(set(versions), {video.video_id})
        self.assertEqual({item.video_id for item in result.citations}, {str(video.video_id)})
        self.assertTrue(any(tail in item.excerpt for item in result.evidence))

    def test_course_turn_retrieves_and_cites_more_than_one_lecture(self) -> None:
        model = FakeAnswerModel(
            "The first lecture introduces attention [S1], while the second "
            "shows its output [S2]."
        )
        with connection(self.database_url) as database:
            first = publish_video_with_evidence(
                database,
                owner_id=self.owner,
                title="Attention foundations",
                url="https://youtu.be/abcdefghijk",
                transcript_vtt=(
                    "WEBVTT\n\n00:00.000 --> 00:10.000\n"
                    "attention uses queries and keys\n"
                ),
            )
            second = publish_video_with_evidence(
                database,
                owner_id=self.owner,
                title="Attention outputs",
                url="https://youtu.be/lmnopqrstuv",
                transcript_vtt=(
                    "WEBVTT\n\n00:00.000 --> 00:10.000\n"
                    "attention produces a weighted output\n"
                ),
            )
            course = create_course(
                database,
                owner_id=self.owner,
                creation_key=uuid4(),
                title="Transformer course",
            )
            for video in (first, second):
                attach_course_lecture(
                    database,
                    course.course_id,
                    video.video_id,
                    owner_id=self.owner,
                )
            state = new_course_conversation_state(course_id=course.course_id)
            stored = create_conversation(
                database,
                owner_id=self.owner,
                course_id=course.course_id,
                selected_video_ids=[first.video_id, second.video_id],
                state=state.model_dump(mode="json"),
                conversation_id=state.conversation_id,
            )
            result, updated, versions = execute_course_turn(
                database,
                "How does attention develop across these lectures?",
                state,
                owner_id=self.owner,
                course_id=course.course_id,
                course_title="Transformer course",
                video_ids=[first.video_id, second.video_id],
                dependencies=VideoAnswerDependencies(model=model),
            )
            turn_index = append_turn(
                database,
                stored["id"],
                owner_id=self.owner,
                course_id=course.course_id,
                versions=versions,
                question=result.question,
                rewritten_query=result.standalone_query or result.question,
                answer=result.answer,
                result=result.model_dump(mode="json"),
                state=updated.model_dump(mode="json"),
                cost_usd=result.cost_usd,
            )
            pinned = database.execute(
                """
                select count(*) as count from video.course_turn_versions
                where owner_id = %s and conversation_id = %s
                """,
                (self.owner, stored["id"]),
            ).fetchone()["count"]

        self.assertEqual({item.video_id for item in result.evidence[:2]}, {str(first.video_id), str(second.video_id)})
        self.assertEqual({item.video_id for item in result.citations}, {str(first.video_id), str(second.video_id)})
        self.assertEqual(set(versions), {first.video_id, second.video_id})
        self.assertEqual(turn_index, 0)
        self.assertEqual(pinned, 2)
        self.assertIn("Lecture 1", model.requests[0][1]["content"])


if __name__ == "__main__":
    unittest.main()
