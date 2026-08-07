"""Side chat endpoints, evidence pinning, and the grounding rule they must keep."""

import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from study.analyze import ANCHORED_QUOTE_INSTRUCTIONS, SYSTEM_PROMPT
from study.contracts import (
    ConversationState,
    QuoteAnchor,
    SideContextReport,
    TurnResult,
)
from study.prompts import DEFAULT_PROMPT_PROFILE
from study.query import ANCHOR_RETRIEVAL_METHOD

OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
PARENT_ID = UUID("22222222-2222-4222-8222-222222222222")
SIDE_CHAT_ID = UUID("33333333-3333-4333-8333-333333333333")

PARENT_RESULT = TurnResult(
    question="What is training-serving skew?",
    answer="Features differ [S1] and the gap compounds [S2].",
    route="retrieval_qa",
    history_dependency="independent",
    standalone_query="What is training-serving skew?",
    evidence=[
        {
            "node_id": 10,
            "pages": [12],
            "path": "Chapter 1",
            "chunk_id": "chunk-one",
            "rank": 1,
        },
        {
            "node_id": 20,
            "pages": [13],
            "path": "Chapter 2",
            "chunk_id": "chunk-two",
            "rank": 2,
        },
    ],
    citations=[{"marker": "[S1]", "node_id": 10, "page": 12, "evidence_rank": 1}],
    outcome="answer",
    retrieval_mode="hybrid_rerank",
)


def conversation_row(
    conversation_id=PARENT_ID,
    *,
    parent=None,
    anchors=(),
    state=None,
):
    return {
        "id": conversation_id,
        "title": "Main study session",
        "book_ids": [1, 2],
        "retrieval_mode": "hybrid_rerank",
        "prompt_profile_json": DEFAULT_PROMPT_PROFILE.model_dump(mode="json"),
        "state_json": state or {},
        "parent_conversation_id": parent,
        "anchors_json": list(anchors),
        "created_at": "2026-08-07T00:00:00Z",
        "updated_at": "2026-08-07T00:00:00Z",
    }


def parent_turn_row(turn_index=0):
    return {
        "turn_index": turn_index,
        "question": PARENT_RESULT.question,
        "answer": PARENT_RESULT.answer,
        "result_json": PARENT_RESULT.model_dump(mode="json"),
        "created_at": "2026-08-07T00:00:00Z",
    }


ANCHOR = {
    "anchor_id": "anchor-one",
    "parent_turn_index": 0,
    "quoted_text": "the gap compounds [S2]",
}


@contextmanager
def stubbed_side_chat_store(**overrides):
    """Serve the conversation store from memory, as the API tests do."""

    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = {
        "turn_count": 0,
        "side_thread_count": 0,
    }
    defaults = {
        "load_conversation": MagicMock(return_value=conversation_row()),
        "load_turns": MagicMock(return_value=[parent_turn_row()]),
        "create_conversation": MagicMock(
            return_value=conversation_row(
                SIDE_CHAT_ID,
                parent=PARENT_ID,
                anchors=[ANCHOR],
            )
        ),
        "set_conversation_state": MagicMock(),
        "list_side_chats": MagicMock(return_value=[]),
        "update_conversation": MagicMock(),
        "append_turn": MagicMock(return_value=0),
    }
    defaults.update(overrides)
    with patch("api.main.database_connection") as open_connection:
        open_connection.return_value.__enter__.return_value = connection
        with (
            patch("api.main.load_conversation", defaults["load_conversation"]),
            patch("api.main.load_turns", defaults["load_turns"]),
            patch("api.main.create_conversation", defaults["create_conversation"]),
            patch(
                "api.main.set_conversation_state",
                defaults["set_conversation_state"],
            ),
            patch("api.main.list_side_chats", defaults["list_side_chats"]),
            patch("api.main.update_conversation", defaults["update_conversation"]),
            patch("api.main.append_turn", defaults["append_turn"]),
        ):
            yield defaults


class SideChatApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        app.dependency_overrides[current_owner] = lambda: OWNER_ID

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_opening_a_side_chat_inherits_the_parents_scope(self):
        with stubbed_side_chat_store() as store:
            response = await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={"anchors": [{"parent_turn_index": 0, "quoted_text": "the gap"}]},
            )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["parent_conversation_id"], str(PARENT_ID))
        self.assertEqual(payload["book_ids"], [1, 2])
        self.assertEqual(payload["retrieval_mode"], "hybrid_rerank")
        created = store["create_conversation"].call_args.kwargs
        self.assertEqual(created["book_ids"], [1, 2])
        self.assertEqual(created["retrieval_mode"], "hybrid_rerank")
        self.assertEqual(created["parent_conversation_id"], PARENT_ID)

    async def test_a_new_side_chat_is_seeded_with_the_anchored_answer(self):
        with stubbed_side_chat_store() as store:
            await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={"anchors": [{"parent_turn_index": 0, "quoted_text": "the gap"}]},
            )

        seeded = ConversationState.model_validate(
            store["set_conversation_state"].call_args.kwargs["state"]
        )
        self.assertEqual(seeded.previous_answer, PARENT_RESULT.answer)
        self.assertEqual(
            [reference.chunk_id for reference in seeded.previous_evidence],
            ["chunk-one", "chunk-two"],
        )
        self.assertEqual(seeded.conversation_id, str(SIDE_CHAT_ID))

    async def test_the_server_assigns_anchor_ids(self):
        with stubbed_side_chat_store() as store:
            await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={
                    "anchors": [
                        {"parent_turn_index": 0, "quoted_text": "one"},
                        {"parent_turn_index": 0, "quoted_text": "two"},
                    ]
                },
            )

        anchors = store["create_conversation"].call_args.kwargs["anchors"]
        ids = {anchor["anchor_id"] for anchor in anchors}
        self.assertEqual(len(ids), 2)
        self.assertNotIn(None, ids)

    async def test_a_marker_only_selection_is_named_after_its_question(self):
        with stubbed_side_chat_store() as store:
            await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={"anchors": [{"parent_turn_index": 0, "quoted_text": "[S1] [S2]"}]},
            )

        self.assertEqual(
            store["create_conversation"].call_args.kwargs["title"],
            PARENT_RESULT.question,
        )

    async def test_a_prose_selection_names_the_side_chat(self):
        with stubbed_side_chat_store() as store:
            await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={
                    "anchors": [
                        {
                            "parent_turn_index": 0,
                            "quoted_text": "the gap compounds [S2]",
                        }
                    ]
                },
            )

        self.assertEqual(
            store["create_conversation"].call_args.kwargs["title"],
            "the gap compounds",
        )

    async def test_a_whole_answer_is_long_enough_to_anchor(self):
        """Answers average over 7,000 characters; the first limit was 4,000.

        Rejecting them made "Ask on the side" fail on roughly half of real
        answers, and the interface reported nothing at all.
        """

        with stubbed_side_chat_store() as store:
            response = await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={
                    "anchors": [
                        {"parent_turn_index": 0, "quoted_text": "word " * 2_000}
                    ]
                },
            )

        self.assertEqual(response.status_code, 201)
        anchors = store["create_conversation"].call_args.kwargs["anchors"]
        self.assertEqual(len(anchors[0]["quoted_text"]), len("word " * 2_000) - 1)

    async def test_a_quote_beyond_the_stored_limit_is_still_refused(self):
        with stubbed_side_chat_store():
            response = await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={
                    "anchors": [
                        {"parent_turn_index": 0, "quoted_text": "x" * 16_001}
                    ]
                },
            )

        self.assertEqual(response.status_code, 422)

    async def test_an_anchor_on_a_turn_the_parent_lacks_is_rejected(self):
        with stubbed_side_chat_store():
            response = await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={"anchors": [{"parent_turn_index": 9, "quoted_text": "ghost"}]},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("not part of the parent", response.json()["detail"])

    async def test_a_side_chat_cannot_be_opened_over_a_side_chat(self):
        row = conversation_row(SIDE_CHAT_ID, parent=PARENT_ID, anchors=[ANCHOR])
        with stubbed_side_chat_store(
            load_conversation=MagicMock(return_value=row),
        ):
            response = await self.client.post(
                f"/api/conversations/{SIDE_CHAT_ID}/side-chats",
                json={"anchors": [{"parent_turn_index": 0, "quoted_text": "nested"}]},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("cannot be nested", response.json()["detail"])

    async def test_opening_a_side_chat_over_a_missing_conversation_is_404(self):
        with stubbed_side_chat_store(
            load_conversation=MagicMock(return_value=None),
        ):
            response = await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={"anchors": [{"parent_turn_index": 0, "quoted_text": "gone"}]},
            )

        self.assertEqual(response.status_code, 404)

    async def test_at_least_one_anchor_is_required(self):
        with stubbed_side_chat_store():
            response = await self.client.post(
                f"/api/conversations/{PARENT_ID}/side-chats",
                json={"anchors": []},
            )

        self.assertEqual(response.status_code, 422)

    async def test_updating_anchors_keeps_the_ids_of_chips_that_remain(self):
        row = conversation_row(SIDE_CHAT_ID, parent=PARENT_ID, anchors=[ANCHOR])
        updated = conversation_row(SIDE_CHAT_ID, parent=PARENT_ID, anchors=[ANCHOR])
        with stubbed_side_chat_store(
            load_conversation=MagicMock(return_value=row),
            update_conversation=MagicMock(return_value=updated),
        ) as store:
            response = await self.client.patch(
                f"/api/side-chats/{SIDE_CHAT_ID}",
                json={
                    "anchors": [
                        {
                            "anchor_id": "anchor-one",
                            "parent_turn_index": 0,
                            "quoted_text": "the gap compounds [S2]",
                        },
                        {"parent_turn_index": 0, "quoted_text": "a second reference"},
                    ]
                },
            )

        self.assertEqual(response.status_code, 200)
        anchors = store["update_conversation"].call_args.kwargs["anchors"]
        self.assertEqual(anchors[0]["anchor_id"], "anchor-one")
        self.assertNotEqual(anchors[1]["anchor_id"], "anchor-one")

    async def test_patching_a_root_conversation_as_a_side_chat_is_404(self):
        with stubbed_side_chat_store():
            response = await self.client.patch(
                f"/api/side-chats/{PARENT_ID}",
                json={"title": "Not a side chat"},
            )

        self.assertEqual(response.status_code, 404)

    async def test_the_main_chat_endpoint_refuses_a_side_chat_id(self):
        row = conversation_row(SIDE_CHAT_ID, parent=PARENT_ID, anchors=[ANCHOR])
        with (
            patch("api.main._require_ready_books"),
            stubbed_side_chat_store(load_conversation=MagicMock(return_value=row)),
        ):
            response = await self.client.post(
                "/api/chat",
                json={
                    "question": "Continue without my anchors",
                    "book_ids": [1],
                    "conversation_id": str(SIDE_CHAT_ID),
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("side-chat turn endpoint", response.json()["detail"])

    async def test_a_side_turn_runs_with_the_anchored_context_and_is_persisted(self):
        row = conversation_row(
            SIDE_CHAT_ID,
            parent=PARENT_ID,
            anchors=[ANCHOR],
            state=ConversationState(
                conversation_id=str(SIDE_CHAT_ID),
                book_ids=[1, 2],
                previous_answer=PARENT_RESULT.answer,
            ).model_dump(mode="json"),
        )
        answer = TurnResult(
            question="What does that mean?",
            answer="It means this [S1]",
            route="retrieval_qa",
            history_dependency="dependent",
            standalone_query="What does compounding skew mean?",
            outcome="answer",
        )
        turn = MagicMock(
            return_value=(
                answer,
                ConversationState(conversation_id="whatever-the-workflow-made-up"),
            )
        )
        with (
            patch("api.main._require_ready_books"),
            patch("api.main.execute_conversation_turn", turn),
            stubbed_side_chat_store(
                load_conversation=MagicMock(return_value=row),
            ) as store,
        ):
            response = await self.client.post(
                f"/api/side-chats/{SIDE_CHAT_ID}/turns/stream",
                json={"question": "What does that mean?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: final", response.text)

        call = turn.call_args
        side_context = call.kwargs["side_context"]
        self.assertEqual(side_context.pinned_chunk_ids, ("chunk-two",))
        self.assertEqual(side_context.anchored_quotes, ("the gap compounds [S2]",))
        self.assertIn("not source evidence", side_context.request_context)
        # Scope, mode and profile come from the stored side chat, not the request.
        self.assertEqual(call.kwargs["book_ids"], [1, 2])
        self.assertEqual(call.kwargs["retrieval_mode"], "hybrid_rerank")
        # A side question defaults to the short answer a small window suits.
        self.assertEqual(call.kwargs["response_depth"], "quick")

        recorded = store["append_turn"].call_args.kwargs
        self.assertEqual(recorded["question"], "What does that mean?")
        self.assertEqual(
            json.loads(json.dumps(recorded["state"]))["conversation_id"],
            str(SIDE_CHAT_ID),
        )

    async def test_a_turn_reports_the_index_it_was_recorded_under(self):
        """The client cannot derive it, and a side chat anchors to it."""

        row = conversation_row(SIDE_CHAT_ID, parent=PARENT_ID, anchors=[ANCHOR])
        turn = MagicMock(
            return_value=(
                TurnResult(
                    question="What does that mean?",
                    answer="It means this [S1]",
                    route="retrieval_qa",
                    history_dependency="dependent",
                    standalone_query="What does that mean?",
                    outcome="answer",
                ),
                ConversationState(conversation_id=str(SIDE_CHAT_ID)),
            )
        )
        with (
            patch("api.main._require_ready_books"),
            patch("api.main.execute_conversation_turn", turn),
            stubbed_side_chat_store(
                load_conversation=MagicMock(return_value=row),
                append_turn=MagicMock(return_value=4),
            ),
        ):
            response = await self.client.post(
                f"/api/side-chats/{SIDE_CHAT_ID}/turns/stream",
                json={"question": "What does that mean?"},
            )

        self.assertIn('"turn_index":4', response.text.replace(" ", ""))

    async def test_a_side_turn_records_what_context_it_was_given(self):
        row = conversation_row(SIDE_CHAT_ID, parent=PARENT_ID, anchors=[ANCHOR])

        def run(question, state, **kwargs):
            report = kwargs["side_context"].report
            return (
                TurnResult(
                    question=question,
                    answer="Answered [S1]",
                    route="retrieval_qa",
                    history_dependency="dependent",
                    standalone_query=question,
                    outcome="answer",
                    side_context=report,
                ),
                state,
            )

        with (
            patch("api.main._require_ready_books"),
            patch("api.main.execute_conversation_turn", side_effect=run),
            stubbed_side_chat_store(
                load_conversation=MagicMock(return_value=row),
            ) as store,
        ):
            await self.client.post(
                f"/api/side-chats/{SIDE_CHAT_ID}/turns/stream",
                json={"question": "What does that mean?"},
            )

        stored = store["append_turn"].call_args.kwargs["result"]
        report = SideContextReport.model_validate(stored["side_context"])
        self.assertEqual(report.anchor_ids, ["anchor-one"])
        self.assertEqual(report.pinned_chunk_ids, ["chunk-two"])
        self.assertGreater(report.token_count, 0)

    async def test_a_turn_on_a_root_conversation_id_is_404(self):
        with (
            patch("api.main._require_ready_books"),
            stubbed_side_chat_store(),
        ):
            response = await self.client.post(
                f"/api/side-chats/{PARENT_ID}/turns/stream",
                json={"question": "Where am I?"},
            )

        self.assertIn("event: error", response.text)
        self.assertIn("side chat not found", response.text)

    async def test_side_chats_are_listed_for_their_parent(self):
        listed = [
            {
                **conversation_row(SIDE_CHAT_ID, parent=PARENT_ID, anchors=[ANCHOR]),
                "turn_count": 3,
            }
        ]
        with stubbed_side_chat_store(
            list_side_chats=MagicMock(return_value=listed),
        ):
            response = await self.client.get(
                f"/api/conversations/{PARENT_ID}/side-chats"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["side_chats"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["turn_count"], 3)
        self.assertEqual(payload[0]["anchors"][0]["anchor_id"], "anchor-one")

    async def test_listing_side_chats_of_a_missing_conversation_is_404(self):
        with stubbed_side_chat_store(
            load_conversation=MagicMock(return_value=None),
        ):
            response = await self.client.get(
                f"/api/conversations/{PARENT_ID}/side-chats"
            )

        self.assertEqual(response.status_code, 404)

    async def test_side_chat_endpoints_require_authentication(self):
        app.dependency_overrides.clear()

        for method, path, payload in (
            ("post", f"/api/conversations/{PARENT_ID}/side-chats", {"anchors": []}),
            ("get", f"/api/conversations/{PARENT_ID}/side-chats", None),
            ("patch", f"/api/side-chats/{SIDE_CHAT_ID}", {"title": "x"}),
            (
                "post",
                f"/api/side-chats/{SIDE_CHAT_ID}/turns/stream",
                {"question": "x"},
            ),
        ):
            with self.subTest(path=path):
                response = await getattr(self.client, method)(
                    path, **({"json": payload} if payload else {})
                )
                self.assertEqual(response.status_code, 401)


class AnchoredRetrievalTests(unittest.TestCase):
    """The pinning mechanism, which is what makes the quote's priority real."""

    def documents(self, pinned_chunk_ids):
        from study.query import _pinned_documents

        row = {
            "id": "chunk-two",
            "source_book_id": 1,
            "source_node_id": 20,
            "toc_index": 2,
            "chunk_index": 0,
            "section_title": "Chapter 2",
            "path_text": "Chapter 2",
            "start_page": 13,
            "end_page": 13,
            "text": "The gap compounds because…",
            "content_types": ["text"],
        }
        connection = MagicMock()
        with (
            patch("study.query.database_connection") as open_connection,
            patch("study.query.chunks_by_id", return_value={"chunk-two": row}),
        ):
            open_connection.return_value.__enter__.return_value = connection
            return _pinned_documents(
                pinned_chunk_ids,
                database_url=None,
                owner_id=OWNER_ID,
                scope=[1, 2],
            )

    def test_a_pinned_chunk_is_indistinguishable_from_retrieved_evidence(self):
        documents = self.documents(["chunk-two"])

        self.assertEqual(len(documents), 1)
        metadata = documents[0].metadata
        self.assertEqual(metadata["chunk_id"], "chunk-two")
        self.assertEqual(metadata["book_id"], 1)
        self.assertEqual(metadata["start_page"], 13)
        self.assertEqual(metadata["retrieval_method"], ANCHOR_RETRIEVAL_METHOD)

    def test_a_chunk_outside_the_frozen_book_scope_is_dropped(self):
        from study.query import _pinned_documents

        row = {
            "id": "chunk-two",
            "source_book_id": 99,
            "source_node_id": 20,
            "toc_index": 2,
            "chunk_index": 0,
            "section_title": "Elsewhere",
            "path_text": "Elsewhere",
            "start_page": 1,
            "end_page": 1,
            "text": "Another book entirely",
            "content_types": ["text"],
        }
        connection = MagicMock()
        with (
            patch("study.query.database_connection") as open_connection,
            patch("study.query.chunks_by_id", return_value={"chunk-two": row}),
        ):
            open_connection.return_value.__enter__.return_value = connection
            documents = _pinned_documents(
                ["chunk-two"],
                database_url=None,
                owner_id=OWNER_ID,
                scope=[1, 2],
            )

        self.assertEqual(documents, [])

    def test_a_chunk_that_no_longer_exists_is_skipped(self):
        self.assertEqual(self.documents(["chunk-gone"]), [])

    def test_no_pins_means_no_database_work(self):
        from study.query import _pinned_documents

        with patch("study.query.database_connection") as open_connection:
            self.assertEqual(
                _pinned_documents([], database_url=None, owner_id=OWNER_ID, scope=None),
                [],
            )
            open_connection.assert_not_called()


class PinnedEvidenceOrderTests(unittest.TestCase):
    def test_pinned_chunks_take_the_lowest_markers_and_are_not_duplicated(self):
        from study.query import _answer_retrieval_question

        def document(chunk_id, page):
            return SimpleNamespace(
                page_content=f"Text of {chunk_id}",
                metadata={
                    "chunk_id": chunk_id,
                    "book_id": 1,
                    "node_id": 1,
                    "path": "Chapter 1",
                    "start_page": page,
                    "end_page": page,
                },
            )

        captured = {}

        def build(**kwargs):
            captured.update(kwargs)
            return [("system", "s"), ("human", "h")]

        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [
            {"id": 1, "title": "A book"}
        ]
        with (
            patch("study.query.database_connection") as open_connection,
            patch("study.query.BookRetriever") as retriever,
            patch(
                "study.query._pinned_documents",
                return_value=[document("chunk-two", 13)],
            ),
            patch("study.query.build_answer_messages", side_effect=build),
            patch("study.query.select_figures", return_value=[]),
            patch(
                "study.query.invoke_with_streaming",
                return_value=SimpleNamespace(content="Answer [S1]"),
            ),
        ):
            open_connection.return_value.__enter__.return_value = connection
            retriever.return_value.invoke.return_value = [
                document("chunk-one", 12),
                document("chunk-two", 13),
            ]
            result = _answer_retrieval_question(
                "What does that mean?",
                database_url=None,
                owner_id=OWNER_ID,
                book_id=None,
                book_ids=[1],
                retrieval_mode="hybrid_rerank",
                model=MagicMock(),
                prompt_profile=DEFAULT_PROMPT_PROFILE,
                response_depth="quick",
                routing_reason="side chat",
                pinned_chunk_ids=["chunk-two"],
                request_context="Passages the reader is asking about…",
            )

        # The pinned chunk leads, so [S1] is the evidence the quoted sentence
        # rested on, and retrieval fills in behind it without repeating it.
        self.assertEqual(
            [reference.chunk_id for reference in result.evidence],
            ["chunk-two", "chunk-one"],
        )
        self.assertEqual(captured["request_context"], "Passages the reader is asking about…")
        # The quoted answer text reaches the model as request context only. It
        # is never part of the evidence the answer may cite.
        self.assertNotIn("Passages the reader", captured["evidence"])


class AnalyserPromptTests(unittest.TestCase):
    def test_the_main_chat_prompt_is_unchanged_by_this_feature(self):
        from study.analyze import _payload

        state = ConversationState(conversation_id="c1")
        payload = _payload("What is skew?", state, [])

        self.assertNotIn("anchored_quotes", payload)

    def test_anchored_quotes_reach_the_analyser_payload(self):
        from study.analyze import _payload

        state = ConversationState(conversation_id="c1")
        payload = _payload("What does that mean?", state, [], ["the gap compounds"])

        self.assertEqual(payload["anchored_quotes"], ["the gap compounds"])

    def test_the_extra_instructions_are_additive(self):
        self.assertNotIn(ANCHORED_QUOTE_INSTRUCTIONS, SYSTEM_PROMPT)
        self.assertIn("anchored_quotes", ANCHORED_QUOTE_INSTRUCTIONS)


class SideChatAnchorValidationTests(unittest.TestCase):
    def test_an_anchor_id_the_row_does_not_have_is_replaced(self):
        from api.main import SideChatAnchorInput, _assigned_anchors

        anchors = _assigned_anchors(
            [
                SideChatAnchorInput(
                    anchor_id="not-mine",
                    parent_turn_index=0,
                    quoted_text="text",
                )
            ],
            existing=[
                QuoteAnchor(anchor_id="mine", parent_turn_index=0, quoted_text="t")
            ],
            turn_indexes={0},
        )

        self.assertNotEqual(anchors[0].anchor_id, "not-mine")

    def test_a_duplicated_anchor_id_is_made_unique(self):
        from api.main import SideChatAnchorInput, _assigned_anchors

        existing = [
            QuoteAnchor(anchor_id="mine", parent_turn_index=0, quoted_text="t")
        ]
        anchors = _assigned_anchors(
            [
                SideChatAnchorInput(
                    anchor_id="mine", parent_turn_index=0, quoted_text="one"
                ),
                SideChatAnchorInput(
                    anchor_id="mine", parent_turn_index=0, quoted_text="two"
                ),
            ],
            existing=existing,
            turn_indexes={0},
        )

        self.assertEqual(anchors[0].anchor_id, "mine")
        self.assertNotEqual(anchors[1].anchor_id, "mine")

    def test_quoted_text_is_stripped(self):
        from api.main import SideChatAnchorInput, _assigned_anchors

        anchors = _assigned_anchors(
            [SideChatAnchorInput(parent_turn_index=0, quoted_text="  spaced  ")],
            existing=[],
            turn_indexes={0},
        )

        self.assertEqual(anchors[0].quoted_text, "spaced")


if __name__ == "__main__":
    unittest.main()
