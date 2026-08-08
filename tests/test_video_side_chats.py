"""Side chats over a lecture: persistence, pinning, and the version they pin to."""

import unittest
from uuid import uuid4

from psycopg.errors import CheckViolation

from storage.database import connection, resolve_database_url
from study.contracts import QuoteAnchor
from study.side_context import build_side_context
from video.answers import VideoAnswerDependencies, retrieve_turn_evidence
from video.analyze import ModelDecision
from video.contracts import VideoTurnResult
from video.conversation import execute_video_turn, new_video_conversation_state
from video.conversation_store import (
    append_turn,
    create_conversation,
    delete_conversation,
    list_conversations,
    list_side_chats,
    load_conversation,
    set_anchors,
    set_conversation_state,
)
from video.side_context import (
    ANCHOR_RETRIEVAL_METHOD,
    merge_pinned,
    parent_turn,
    parent_turns,
    pinned_evidence,
)
from tests.test_video_conversation import FakeAnalysisModel, FakeAnswerModel
from tests.video_fixtures import publish_video_with_evidence

ANCHOR = {
    "anchor_id": "anchor-one",
    "parent_turn_index": 0,
    "quoted_text": "the lecturer draws the attention diagram [S1]",
}


class VideoSideChatStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-side-chat.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)
            self.parent = create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="What does the diagram show?",
            )

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def side_chat(self, *, parent_id=None, anchors=(ANCHOR,)):
        with connection(self.database_url) as database:
            return create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="What does that mean?",
                parent_conversation_id=parent_id or self.parent["id"],
                anchors=anchors,
            )

    def test_a_side_chat_records_its_parent_and_anchors(self) -> None:
        side = self.side_chat()

        self.assertEqual(side["parent_conversation_id"], self.parent["id"])
        self.assertEqual(side["anchors_json"], [ANCHOR])
        # Inherited, not chosen: a side chat is about a passage of this lecture.
        self.assertEqual(str(side["video_id"]), str(self.video.video_id))

    def test_a_root_conversation_has_no_parent_and_no_anchors(self) -> None:
        self.assertIsNone(self.parent["parent_conversation_id"])
        self.assertEqual(self.parent["anchors_json"], [])

    def test_a_side_chat_of_a_side_chat_is_rejected(self) -> None:
        side = self.side_chat()

        with self.assertRaises(CheckViolation):
            self.side_chat(parent_id=side["id"])

    def test_anchors_without_a_parent_are_refused_before_the_database(self) -> None:
        with self.assertRaises(ValueError):
            with connection(self.database_url) as database:
                create_conversation(
                    database,
                    owner_id=self.owner,
                    video_id=self.video.video_id,
                    title="Orphan anchor",
                    anchors=(ANCHOR,),
                )

    def test_the_conversation_list_shows_roots_and_counts_side_chats(self) -> None:
        self.side_chat()
        self.side_chat(anchors=({**ANCHOR, "anchor_id": "anchor-two"},))

        with connection(self.database_url) as database:
            listed = list_conversations(database, owner_id=self.owner)

        self.assertEqual([row["id"] for row in listed], [self.parent["id"]])
        self.assertEqual(listed[0]["side_thread_count"], 2)

    def test_side_chats_are_listed_for_their_parent(self) -> None:
        side = self.side_chat()

        with connection(self.database_url) as database:
            listed = list_side_chats(database, self.parent["id"], owner_id=self.owner)

        self.assertEqual([row["id"] for row in listed], [side["id"]])
        self.assertEqual(listed[0]["anchors_json"], [ANCHOR])
        self.assertEqual(listed[0]["turn_count"], 0)

    def test_deleting_the_parent_removes_its_side_chats(self) -> None:
        side = self.side_chat()

        with connection(self.database_url) as database:
            self.assertTrue(
                delete_conversation(database, self.parent["id"], owner_id=self.owner)
            )
            self.assertIsNone(
                load_conversation(database, side["id"], owner_id=self.owner)
            )

    def test_anchors_are_replaced_wholesale(self) -> None:
        side = self.side_chat()
        replacement = {
            "anchor_id": "anchor-two",
            "parent_turn_index": 0,
            "quoted_text": "a different sentence",
        }

        with connection(self.database_url) as database:
            updated = set_anchors(
                database, side["id"], owner_id=self.owner, anchors=(replacement,)
            )

        self.assertEqual(updated["anchors_json"], [replacement])

    def test_a_root_conversation_cannot_be_given_anchors(self) -> None:
        with connection(self.database_url) as database:
            # The statement is scoped to side chats, so a root is untouched
            # rather than quietly acquiring anchors that mean nothing.
            self.assertIsNone(
                set_anchors(
                    database,
                    self.parent["id"],
                    owner_id=self.owner,
                    anchors=(ANCHOR,),
                )
            )

    def test_seeded_state_is_stored_and_readable(self) -> None:
        side = self.side_chat()
        state = new_video_conversation_state(
            video_id=str(self.video.video_id), conversation_id=side["id"]
        )
        state.previous_answer = "The answer being asked about [S1]"

        with connection(self.database_url) as database:
            set_conversation_state(
                database,
                side["id"],
                owner_id=self.owner,
                state=state.model_dump(mode="json"),
            )
            stored = load_conversation(database, side["id"], owner_id=self.owner)

        self.assertEqual(
            stored["state_json"]["previous_answer"],
            "The answer being asked about [S1]",
        )
        # `video.conversations` stamps `updated_at` on every update by trigger,
        # so seeding moves it. A freshly created side chat sorting first among
        # its siblings is the only consequence.
        self.assertGreaterEqual(stored["updated_at"], side["updated_at"])


class VideoPinnedEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-pin.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def retrieved(self, database, **kwargs):
        return retrieve_turn_evidence(
            database,
            owner_id=self.owner,
            video_id=self.video.video_id,
            query="what does the softmax diagram show?",
            dependencies=VideoAnswerDependencies(),
            **kwargs,
        )

    def test_a_pinned_unit_leads_the_evidence_and_is_marked_as_anchored(self) -> None:
        with connection(self.database_url) as database:
            baseline = self.retrieved(database)
            target = baseline.evidence[-1].evidence_id
            anchored = self.retrieved(database, pinned_evidence_ids=[target])

        self.assertEqual(anchored.evidence[0].evidence_id, target)
        self.assertEqual(anchored.evidence[0].rank, 1)
        self.assertEqual(
            anchored.evidence[0].retrieval_method, ANCHOR_RETRIEVAL_METHOD
        )
        # Ranks describe the list the model is given, so they are contiguous.
        self.assertEqual(
            [item.rank for item in anchored.evidence],
            list(range(1, len(anchored.evidence) + 1)),
        )
        self.assertEqual(anchored.dropped_anchors, ())

    def test_a_pinned_unit_retrieval_also_found_appears_once(self) -> None:
        with connection(self.database_url) as database:
            baseline = self.retrieved(database)
            target = baseline.evidence[0].evidence_id
            anchored = self.retrieved(database, pinned_evidence_ids=[target])

        ids = [item.evidence_id for item in anchored.evidence]
        self.assertEqual(ids.count(target), 1)

    def test_an_anchor_this_version_does_not_have_is_reported(self) -> None:
        missing = "f" * 64
        with connection(self.database_url) as database:
            anchored = self.retrieved(database, pinned_evidence_ids=[missing])

        self.assertEqual(anchored.dropped_anchors, (missing,))
        self.assertTrue(anchored.evidence)

    def test_pinning_asks_the_database_for_nothing_when_there_is_nothing(self) -> None:
        with connection(self.database_url) as database:
            units, missing = pinned_evidence(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                ingestion_version_id=self.video.version_id,
                evidence_ids=[],
            )

        self.assertEqual((units, missing), ([], ()))

    def test_another_owner_cannot_pin_these_units(self) -> None:
        stranger = uuid4()
        with connection(self.database_url) as database:
            baseline = self.retrieved(database)
            units, missing = pinned_evidence(
                database,
                owner_id=stranger,
                video_id=self.video.video_id,
                ingestion_version_id=self.video.version_id,
                evidence_ids=[baseline.evidence[0].evidence_id],
            )

        self.assertEqual(units, [])
        self.assertEqual(len(missing), 1)


class MergePinnedTests(unittest.TestCase):
    """Ordering and renumbering, without a database."""

    class Unit:
        def __init__(self, identifier: str) -> None:
            self.id = identifier
            self.rank = 0

        def __eq__(self, other) -> bool:
            return isinstance(other, type(self)) and other.id == self.id

    def units(self, *ids):
        from dataclasses import replace

        from video.retrieval import VideoEvidence

        return [
            VideoEvidence(
                id=identifier,
                modality="transcript",
                text=identifier,
                start_ms=0,
                end_ms=1,
                page_number=None,
                transcript_segment_id=None,
                frame_id=None,
                visual_event_id=None,
                resource_page_id=None,
                score=0.5,
                retrieval_method="fts",
            )
            for identifier in ids
        ] and [
            replace(
                VideoEvidence(
                    id=identifier,
                    modality="transcript",
                    text=identifier,
                    start_ms=0,
                    end_ms=1,
                    page_number=None,
                    transcript_segment_id=None,
                    frame_id=None,
                    visual_event_id=None,
                    resource_page_id=None,
                    score=0.5,
                    retrieval_method="fts",
                )
            )
            for identifier in ids
        ]

    def test_pinned_units_take_the_lowest_ranks(self) -> None:
        merged = merge_pinned(
            self.units("pinned"), self.units("found-a", "found-b"), limit=8
        )

        self.assertEqual([unit.id for unit in merged], ["pinned", "found-a", "found-b"])
        self.assertEqual([unit.rank for unit in merged], [1, 2, 3])

    def test_the_limit_covers_the_whole_set(self) -> None:
        merged = merge_pinned(
            self.units("pinned"), self.units("a", "b", "c"), limit=2
        )

        self.assertEqual([unit.id for unit in merged], ["pinned", "a"])

    def test_a_duplicate_keeps_its_pinned_position(self) -> None:
        merged = merge_pinned(
            self.units("shared"), self.units("shared", "other"), limit=8
        )

        self.assertEqual([unit.id for unit in merged], ["shared", "other"])


class VideoParentTurnTests(unittest.TestCase):
    def result(self, **overrides) -> VideoTurnResult:
        payload = {
            "question": "What does the diagram show?",
            "answer": "It shows attention [S1] over the sequence [S2].",
            "route": "evidence_qa",
            "history_dependency": "independent",
            "outcome": "answer",
            "evidence": [
                {
                    "rank": 1,
                    "evidence_id": "a" * 64,
                    "modality": "transcript",
                    "excerpt": "attention",
                    "retrieval_method": "fts",
                    "score": 0.5,
                    "start_ms": 1000,
                },
                {
                    "rank": 2,
                    "evidence_id": "b" * 64,
                    "modality": "visual_frame",
                    "excerpt": "diagram",
                    "retrieval_method": "image_vector",
                    "score": 0.4,
                    "frame_id": 7,
                },
            ],
            "citations": [
                {
                    "marker": "[S1]",
                    "evidence_rank": 1,
                    "modality": "transcript",
                    "start_ms": 1000,
                }
            ],
        }
        payload.update(overrides)
        return VideoTurnResult.model_validate(payload)

    def test_a_marker_resolves_to_the_evidence_unit_it_named(self) -> None:
        turn = parent_turn(0, "Q", "A", self.result())
        context = build_side_context(
            [
                QuoteAnchor(
                    anchor_id="a1",
                    parent_turn_index=0,
                    quoted_text="over the sequence [S2]",
                )
            ],
            [turn],
        )

        self.assertEqual(context.pinned_chunk_ids, ("b" * 64,))

    def test_an_uncited_selection_falls_back_to_what_the_answer_cited(self) -> None:
        turn = parent_turn(0, "Q", "A", self.result())
        context = build_side_context(
            [
                QuoteAnchor(
                    anchor_id="a1",
                    parent_turn_index=0,
                    quoted_text="it shows attention over the sequence",
                )
            ],
            [turn],
        )

        self.assertEqual(context.pinned_chunk_ids, ("a" * 64,))

    def test_a_turn_without_an_answer_cannot_be_anchored_to(self) -> None:
        rows = [
            {"turn_index": 0, "question": "Q", "answer": None, "result_json": {}},
            {
                "turn_index": 1,
                "question": "Q",
                "answer": "A [S1]",
                "result_json": self.result().model_dump(mode="json"),
            },
        ]

        self.assertEqual([turn.turn_index for turn in parent_turns(rows)], [1])


class VideoSideTurnTests(unittest.TestCase):
    """One side turn end to end, with a fake answer model."""

    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-side-turn.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)

    def tearDown(self) -> None:
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    def test_a_side_turn_pins_what_the_quote_cited_and_records_the_context(self) -> None:
        model = FakeAnswerModel("Because the pinned frame shows it [S1].")
        with connection(self.database_url) as database:
            parent = create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="What does the diagram show?",
            )
            first, state = execute_video_turn(
                database,
                "what does the softmax diagram show?",
                new_video_conversation_state(
                    video_id=str(self.video.video_id), conversation_id=parent["id"]
                ),
                owner_id=self.owner,
                video_id=self.video.video_id,
                video_title=self.video.title,
                dependencies=VideoAnswerDependencies(
                    model=FakeAnswerModel("It shows attention [S1] and more [S2].")
                ),
            )
            append_turn(
                database,
                parent["id"],
                owner_id=self.owner,
                video_id=self.video.video_id,
                ingestion_version_id=first.ingestion_version_id,
                question=first.question,
                rewritten_query=first.standalone_query or first.question,
                answer=first.answer,
                result=first.model_dump(mode="json"),
                state=state.model_dump(mode="json"),
                cost_usd=first.cost_usd,
                trace_id=first.trace_id,
                maximum_cost_usd=0.05,
            )

            # The reader highlights the sentence carrying [S2].
            anchors = [
                QuoteAnchor(
                    anchor_id="a1",
                    parent_turn_index=0,
                    quoted_text="and more [S2]",
                )
            ]
            side_context = build_side_context(
                anchors,
                parent_turns(
                    [
                        {
                            "turn_index": 0,
                            "question": first.question,
                            "answer": first.answer,
                            "result_json": first.model_dump(mode="json"),
                        }
                    ]
                ),
            )
            expected = first.evidence[1].evidence_id
            result, _ = execute_video_turn(
                database,
                "why does that matter?",
                new_video_conversation_state(
                    video_id=str(self.video.video_id), conversation_id=parent["id"]
                ),
                owner_id=self.owner,
                video_id=self.video.video_id,
                video_title=self.video.title,
                dependencies=VideoAnswerDependencies(model=model),
                analysis_model=FakeAnalysisModel(
                    ModelDecision(
                        route="evidence_qa",
                        history_dependency="dependent",
                        standalone_query=(
                            "why does the attention diagram matter in this lecture?"
                        ),
                        reason="Question about a highlighted passage.",
                    )
                ),
                side_context=side_context,
            )

        self.assertEqual(side_context.pinned_chunk_ids, (expected,))
        self.assertEqual(result.evidence[0].evidence_id, expected)
        self.assertEqual(result.evidence[0].retrieval_method, ANCHOR_RETRIEVAL_METHOD)
        self.assertIsNotNone(result.side_context)
        self.assertEqual(result.side_context.anchor_ids, ["a1"])
        self.assertEqual(result.side_context.pinned_chunk_ids, [expected])
        self.assertGreater(result.side_context.token_count, 0)
        # The quoted passage reaches the model as labelled context, never as
        # evidence it may cite.
        prompt = "\n".join(
            str(part) for message in model.requests[0] for part in [message]
        )
        self.assertIn("not source evidence", prompt)


class VideoTurnRequestContractTests(unittest.TestCase):
    def test_a_lecture_turn_takes_no_answer_depth(self) -> None:
        """Pins why the client must not send one.

        The lecture chat has no depth concept and its request contract forbids
        unknown fields, so a client sending `response_depth` anyway failed every
        lecture side turn with a validation error — which the window then
        rendered as "[object Object]".
        """

        from pydantic import ValidationError

        from api.video_chat import AskRequest

        self.assertEqual(AskRequest(question="why?").question, "why?")
        with self.assertRaises(ValidationError):
            AskRequest(question="why?", response_depth="quick")


class VideoSideTurnReportTests(unittest.TestCase):
    """Every route a side chat can take reports the context it was given."""

    def test_a_clarification_still_records_the_context(self) -> None:
        # Found by driving a real lecture: asking "why does that matter?" about
        # an abstention clarifies, and the report was dropped on that path — so
        # the inspector said nothing about a turn that did use anchored context.
        from video.conversation import _side_report, clarify
        from video.contracts import VideoTurnDecision

        side = build_side_context(
            [
                QuoteAnchor(
                    anchor_id="a1", parent_turn_index=0, quoted_text="a passage"
                )
            ],
            [],
        )

        class Runtime:
            def __init__(self, context) -> None:
                self.context = context

        class Context:
            side_context = side

        output = clarify(
            {
                "question": "why does that matter?",
                "conversation": None,
                "decision": VideoTurnDecision(
                    route="clarify",
                    history_dependency="ambiguous",
                    clarification_question="What does “that” refer to?",
                    reason="No referent.",
                ),
            },
            Runtime(Context()),
        )

        self.assertIsNotNone(output["result"].side_context)
        self.assertEqual(output["result"].side_context.anchor_ids, ["a1"])
        self.assertIsNone(_side_report(None))


class VideoSideChatApiTests(unittest.IsolatedAsyncioTestCase):
    """The HTTP surface, against the real database."""

    async def asyncSetUp(self) -> None:
        from httpx import ASGITransport, AsyncClient

        from api.auth import current_owner
        from api.main import app

        self.app = app
        self.database_url = resolve_database_url()
        self.owner = uuid4()
        with connection(self.database_url) as database:
            database.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (self.owner, f"{self.owner}@video-side-api.test"),
            )
            self.video = publish_video_with_evidence(database, owner_id=self.owner)
            self.parent = create_conversation(
                database,
                owner_id=self.owner,
                video_id=self.video.video_id,
                title="What does the diagram show?",
            )
            result = VideoTurnResult.model_validate(
                {
                    "question": "What does the diagram show?",
                    "answer": "It shows attention [S1].",
                    "route": "evidence_qa",
                    "history_dependency": "independent",
                    "outcome": "answer",
                    "ingestion_version_id": str(self.video.version_id),
                    "evidence": [
                        {
                            "rank": 1,
                            "evidence_id": "a" * 64,
                            "modality": "transcript",
                            "excerpt": "attention",
                            "retrieval_method": "fts",
                            "score": 0.5,
                            "start_ms": 1000,
                        }
                    ],
                    "citations": [
                        {
                            "marker": "[S1]",
                            "evidence_rank": 1,
                            "modality": "transcript",
                            "start_ms": 1000,
                        }
                    ],
                }
            )
            append_turn(
                database,
                self.parent["id"],
                owner_id=self.owner,
                video_id=self.video.video_id,
                ingestion_version_id=str(self.video.version_id),
                question=result.question,
                rewritten_query=result.question,
                answer=result.answer,
                result=result.model_dump(mode="json"),
                state={},
                cost_usd=0.001,
                trace_id=None,
                maximum_cost_usd=0.05,
            )
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: self.owner

    async def asyncTearDown(self) -> None:
        self.app.dependency_overrides.clear()
        await self.client.aclose()
        with connection(self.database_url) as database:
            database.execute("delete from auth.users where id = %s", (self.owner,))

    async def test_opening_a_side_chat_inherits_the_lecture(self) -> None:
        response = await self.client.post(
            f"/api/video-conversations/{self.parent['id']}/side-chats",
            json={
                "anchors": [
                    {"parent_turn_index": 0, "quoted_text": "It shows attention [S1]."}
                ]
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["parent_conversation_id"], str(self.parent["id"]))
        self.assertEqual(payload["video_id"], str(self.video.video_id))
        self.assertEqual(payload["anchors"][0]["parent_turn_index"], 0)
        self.assertEqual(payload["turn_count"], 0)

    async def test_an_anchor_on_a_turn_the_parent_lacks_is_rejected(self) -> None:
        response = await self.client.post(
            f"/api/video-conversations/{self.parent['id']}/side-chats",
            json={"anchors": [{"parent_turn_index": 9, "quoted_text": "ghost"}]},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("not an answered turn", response.json()["detail"])

    async def test_a_side_chat_cannot_be_opened_over_a_side_chat(self) -> None:
        first = await self.client.post(
            f"/api/video-conversations/{self.parent['id']}/side-chats",
            json={"anchors": [{"parent_turn_index": 0, "quoted_text": "attention"}]},
        )
        nested = await self.client.post(
            f"/api/video-conversations/{first.json()['conversation_id']}/side-chats",
            json={"anchors": [{"parent_turn_index": 0, "quoted_text": "nested"}]},
        )

        self.assertEqual(nested.status_code, 422)
        self.assertIn("cannot be nested", nested.json()["detail"])

    async def test_side_chats_are_listed_and_countable_on_the_parent(self) -> None:
        await self.client.post(
            f"/api/video-conversations/{self.parent['id']}/side-chats",
            json={"anchors": [{"parent_turn_index": 0, "quoted_text": "attention"}]},
        )

        listed = await self.client.get(
            f"/api/video-conversations/{self.parent['id']}/side-chats"
        )
        conversations = await self.client.get(
            f"/api/videos/{self.video.video_id}/conversations"
        )

        self.assertEqual(len(listed.json()["side_chats"]), 1)
        rows = conversations.json()["conversations"]
        self.assertEqual([row["conversation_id"] for row in rows], [str(self.parent["id"])])
        self.assertEqual(rows[0]["side_thread_count"], 1)

    async def test_anchors_can_be_replaced_and_keep_known_ids(self) -> None:
        created = await self.client.post(
            f"/api/video-conversations/{self.parent['id']}/side-chats",
            json={"anchors": [{"parent_turn_index": 0, "quoted_text": "attention"}]},
        )
        existing = created.json()["anchors"][0]

        updated = await self.client.patch(
            f"/api/video-side-chats/{created.json()['conversation_id']}",
            json={
                "anchors": [
                    {
                        "anchor_id": existing["anchor_id"],
                        "parent_turn_index": 0,
                        "quoted_text": "attention",
                    },
                    {"parent_turn_index": 0, "quoted_text": "a second passage"},
                ]
            },
        )

        anchors = updated.json()["anchors"]
        self.assertEqual(anchors[0]["anchor_id"], existing["anchor_id"])
        self.assertNotEqual(anchors[1]["anchor_id"], existing["anchor_id"])

    async def test_patching_a_root_conversation_as_a_side_chat_is_404(self) -> None:
        response = await self.client.patch(
            f"/api/video-side-chats/{self.parent['id']}",
            json={"title": "Not a side chat"},
        )

        self.assertEqual(response.status_code, 404)

    async def test_side_chat_endpoints_require_authentication(self) -> None:
        self.app.dependency_overrides.clear()

        for method, path, payload in (
            (
                "post",
                f"/api/video-conversations/{self.parent['id']}/side-chats",
                {"anchors": []},
            ),
            ("get", f"/api/video-conversations/{self.parent['id']}/side-chats", None),
            ("patch", f"/api/video-side-chats/{uuid4()}", {"title": "x"}),
            ("post", f"/api/video-side-chats/{uuid4()}/turns/stream", {"question": "x"}),
        ):
            with self.subTest(path=path):
                response = await getattr(self.client, method)(
                    path, **({"json": payload} if payload else {})
                )
                self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
