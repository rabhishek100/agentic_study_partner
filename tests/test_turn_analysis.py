import json
import unittest

from storage.database import connection as database_connection
from storage.postgres import ingest_book
from study.analyze import ConversationDecisionError, ModelDecision, analyze_turn
from study.contracts import (
    ConversationMessage,
    ConversationState,
    EvidenceRef,
    ScopeRef,
)
from tests.postgres import PostgresOwnerMixin
from tests.test_scope_candidates import FILE_HASH, hierarchy_book


class FakeModel:
    def __init__(self, *outcomes):
        self.outcomes = outcomes
        self.calls = []

    def invoke(self, messages, config=None):
        self.calls.append((messages, config))
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FailIfCalled:
    def invoke(self, messages, config=None):
        raise AssertionError("deterministic routing called the model")


class ConversationDecisionTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self):
        self.setUpPostgresOwner()
        with database_connection(self.database_url) as connection:
            self.book_id = ingest_book(
                connection,
                hierarchy_book(),
                owner_id=self.owner_id,
                title="Hierarchy Book",
                author="Test Author",
                file_hash=FILE_HASH,
                page_count=10,
                parser_version="test-v1",
            )
            self.nodes = {
                row["title"]: dict(row)
                for row in connection.execute(
                    "SELECT * FROM nodes WHERE owner_id = %s",
                    (self.owner_id,),
                )
            }

    def tearDown(self):
        self.tearDownPostgresOwner()

    def state(self, **updates):
        values = {
            "conversation_id": "conversation-1",
            "book_ids": [self.book_id],
        }
        values.update(updates)
        return ConversationState(**values)

    def scope(self, title, kind="section"):
        node = self.nodes[title]
        return ScopeRef(
            kind=kind,
            book_id=self.book_id,
            node_id=node["id"],
            display_path=node["path_text"],
            start_page=node["start_page"],
            end_page=node["end_page"],
        )

    def analyze(self, question, state, model):
        return analyze_turn(
            question,
            state,
            self.database_url,
            owner_id=self.owner_id,
            model=model,
        )

    def test_explicit_hierarchy_requests_are_deterministic(self):
        summary = self.analyze("Summarize Chapter 3.", self.state(), FailIfCalled())
        listing = self.analyze(
            "What sections are present in Chapter 7?",
            self.state(),
            FailIfCalled(),
        )
        chapters = self.analyze(
            "what chapters does this book have",
            self.state(),
            FailIfCalled(),
        )
        natural_chapters = self.analyze(
            "what are the chapters in the Hierarchy Book?",
            self.state(),
            FailIfCalled(),
        )
        mentioned_chapters = self.analyze(
            "show me the chapters from @[Hierarchy Book]",
            self.state(),
            FailIfCalled(),
        )

        self.assertEqual(summary.route, "hierarchy_summary")
        self.assertEqual(
            summary.resolved_scope.node_id,
            self.nodes["Chapter 3. Data Engineering Fundamentals"]["id"],
        )
        self.assertEqual(listing.route, "hierarchy_list")
        self.assertEqual(chapters.route, "hierarchy_list")
        self.assertEqual(chapters.resolved_scope.kind, "book")
        self.assertIsNone(chapters.resolved_scope.node_id)
        self.assertEqual(natural_chapters.route, "hierarchy_list")
        self.assertEqual(natural_chapters.resolved_scope.book_id, self.book_id)
        self.assertEqual(mentioned_chapters.route, "hierarchy_list")
        self.assertEqual(mentioned_chapters.resolved_scope.book_id, self.book_id)

    def test_library_listing_is_deterministic_and_needs_no_scope(self):
        for question in (
            "list all the papers uploaded",
            "Show me all papers in my library.",
            "What papers do I have?",
        ):
            decision = self.analyze(question, self.state(), FailIfCalled())

            self.assertEqual(decision.route, "library_list")
            self.assertEqual(decision.history_dependency, "independent")
            self.assertIsNone(decision.resolved_scope)

    def test_explain_pdf_routes_to_the_complete_selected_document(self):
        decision = self.analyze(
            "Explain this PDF.",
            self.state(),
            FailIfCalled(),
        )

        self.assertEqual(decision.route, "hierarchy_summary")
        self.assertEqual(decision.resolved_scope.kind, "book")
        self.assertEqual(decision.resolved_scope.book_id, self.book_id)
        self.assertIsNone(decision.resolved_scope.node_id)

    def test_ordinal_chapter_is_resolved_by_toc_order_without_the_model(self):
        decision = self.analyze(
            "Explain the first chapter.",
            self.state(),
            FailIfCalled(),
        )

        self.assertEqual(decision.route, "hierarchy_summary")
        self.assertEqual(
            decision.resolved_scope.node_id,
            self.nodes["Chapter 1. Overview"]["id"],
        )
        self.assertIn("table-of-contents order", decision.reason)

        second = self.analyze(
            "Review the second chapter.",
            self.state(),
            FailIfCalled(),
        )
        # The second canonical chapter is numbered 3, proving that ordinals
        # use TOC order rather than being rewritten as chapter numbers.
        self.assertEqual(
            second.resolved_scope.node_id,
            self.nodes["Chapter 3. Data Engineering Fundamentals"]["id"],
        )

    def test_book_reply_completes_a_pending_ordinal_chapter_request(self):
        decision = self.analyze(
            "hierarchy book",
            self.state(pending_clarification="explain the first chapter"),
            FailIfCalled(),
        )

        self.assertEqual(decision.route, "hierarchy_summary")
        self.assertEqual(decision.history_dependency, "dependent")
        self.assertEqual(
            decision.resolved_scope.node_id,
            self.nodes["Chapter 1. Overview"]["id"],
        )

    def test_model_rewrites_followup_and_selects_only_canonical_scope(self):
        active = self.scope("Low-Rank Factorization")
        model = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": (
                    "What does Low-Rank Factorization in Chapter 7 cover?"
                ),
                "scope_node_id": active.node_id,
                "reason": "The follow-up refers to the active section.",
            }
        )

        decision = self.analyze(
            "What does that section cover?",
            self.state(active_scope=active),
            model,
        )

        self.assertEqual(decision.resolved_scope, active)
        self.assertEqual(decision.history_dependency, "dependent")

    def test_ambiguous_reference_can_request_clarification(self):
        model = FakeModel(
            {
                "route": "clarify",
                "history_dependency": "ambiguous",
                "clarification_question": "Which approach do you mean?",
                "reason": "No prior list establishes the ordinal.",
            }
        )

        decision = self.analyze("Explain the second approach.", self.state(), model)

        self.assertEqual(decision.route, "clarify")
        self.assertEqual(decision.clarification_question, "Which approach do you mean?")

    def test_agent_can_infer_a_natural_verbatim_request(self):
        chapter = self.nodes["Chapter 7. Model Deployment and Prediction Service"]
        model = FakeModel(
            {
                "route": "verbatim_reading",
                "history_dependency": "independent",
                "scope_node_id": chapter["id"],
                "reason": "The reader wants the stored chapter text in the chat.",
            }
        )

        decision = self.analyze(
            "Let me read all of chapter 7 here instead of a summary.",
            self.state(),
            model,
        )

        self.assertEqual(decision.route, "verbatim_reading")
        self.assertEqual(decision.resolved_scope.node_id, chapter["id"])

    def test_agentic_verbatim_route_still_requires_a_canonical_scope(self):
        model = FakeModel(
            {
                "route": "verbatim_reading",
                "history_dependency": "independent",
                "reason": "The reader wants source text but named no known scope.",
            }
        )

        decision = self.analyze(
            "Put the entire missing appendix here without summarizing it.",
            self.state(),
            model,
        )

        self.assertEqual(decision.route, "clarify")
        self.assertIn("could not find", decision.clarification_question)

    def test_supplied_clarification_cannot_repeat_the_same_clarify_route(self):
        model = FakeModel(
            {
                "route": "clarify",
                "history_dependency": "ambiguous",
                "clarification_question": "Which approach do you mean?",
                "reason": "The ordinal is ambiguous.",
            }
        )

        decision = self.analyze(
            "I mean the first dataflow mode in Chapter 3.",
            self.state(pending_clarification="Explain the first approach."),
            model,
        )

        self.assertEqual(decision.route, "retrieval_qa")
        self.assertEqual(decision.history_dependency, "independent")
        self.assertIn("Data Passing Through Services", decision.standalone_query)
        self.assertEqual(
            decision.resolved_scope.node_id,
            self.nodes["Chapter 3. Data Engineering Fundamentals"]["id"],
        )

    def test_previous_evidence_resolves_that_section_after_abstention(self):
        model = FakeModel(
            {
                "route": "clarify",
                "history_dependency": "ambiguous",
                "clarification_question": "Which section?",
                "reason": "The section is unclear.",
            }
        )
        evidence = EvidenceRef(
            node_id=self.nodes["Low-Rank Factorization"]["id"],
            pages=[8],
            path="Chapter 7. Model Compression :: Low-Rank Factorization",
        )

        decision = self.analyze(
            "Then what does that section actually cover?",
            self.state(previous_evidence=[evidence]),
            model,
        )

        self.assertEqual(decision.route, "retrieval_qa")
        self.assertEqual(decision.history_dependency, "dependent")
        self.assertIn("Low-Rank Factorization", decision.standalone_query)

    def test_active_scope_and_evidence_resolve_ordinal_comparison(self):
        model = FakeModel(
            {
                "route": "clarify",
                "history_dependency": "dependent",
                "clarification_question": "Which first approach?",
                "reason": "The ordinal is unclear.",
            }
        )
        scope = self.scope("Chapter 3. Data Engineering Fundamentals", "chapter")
        evidence = EvidenceRef(
            node_id=self.nodes["Data Passing Through Services"]["id"],
            pages=[4],
            path=(
                "Chapter 3. Data Engineering Fundamentals :: Modes of Dataflow "
                ":: Data Passing Through Services"
            ),
        )

        decision = self.analyze(
            "Compare it with the first one.",
            self.state(active_scope=scope, previous_evidence=[evidence]),
            model,
        )

        self.assertEqual(decision.route, "retrieval_qa")
        self.assertEqual(decision.resolved_scope, scope)
        self.assertIn("Data Passing Through Services", decision.standalone_query)

    def test_named_topic_outside_active_scope_is_an_independent_switch(self):
        active = self.scope("Chapter 3. Data Engineering Fundamentals", "chapter")
        model = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": "How does reservoir sampling work?",
                "scope_node_id": self.nodes["Reservoir Sampling"]["id"],
                "reason": "It may refer to prior context.",
            }
        )

        decision = self.analyze(
            "How does reservoir sampling work?",
            self.state(active_scope=active),
            model,
        )

        self.assertEqual(decision.history_dependency, "independent")
        self.assertIsNone(decision.resolved_scope)

    def test_long_self_contained_scenario_is_not_repeatedly_clarified(self):
        model = FakeModel(
            {
                "route": "clarify",
                "history_dependency": "ambiguous",
                "clarification_question": "Which campaign?",
                "reason": "The campaign is unclear.",
            }
        )
        question = (
            "A campaign brings in wealthier users, but conversion at a fixed "
            "income is unchanged. What changed?"
        )

        decision = self.analyze(question, self.state(), model)

        self.assertEqual(decision.route, "retrieval_qa")
        self.assertEqual(decision.history_dependency, "independent")
        self.assertIn("distribution shift", decision.standalone_query)
        self.assertIn("conditional on income", decision.standalone_query)

    def test_revise_named_section_normalizes_to_hierarchy_summary(self):
        model = FakeModel(
            {
                "route": "hierarchy_list",
                "history_dependency": "independent",
                "scope_node_id": self.nodes[
                    "Chapter 7. Model Deployment and Prediction Service"
                ]["id"],
                "reason": "List the chapter.",
            }
        )

        decision = self.analyze(
            "Help me revise the Low-Rank Factorization section in Chapter 7.",
            self.state(),
            model,
        )

        self.assertEqual(decision.route, "hierarchy_summary")
        self.assertEqual(
            decision.resolved_scope.node_id,
            self.nodes["Low-Rank Factorization"]["id"],
        )

    def test_negated_section_does_not_override_the_selected_chapter(self):
        chapter = self.nodes["Chapter 7. Model Deployment and Prediction Service"]
        model = FakeModel(
            {
                "route": "hierarchy_summary",
                "history_dependency": "independent",
                "scope_node_id": chapter["id"],
                "reason": "Review the named chapter.",
            }
        )

        decision = self.analyze(
            "Turn the model-deployment chapter into an interview review, "
            "not a section summary.",
            self.state(),
            model,
        )

        self.assertEqual(decision.route, "hierarchy_summary")
        self.assertEqual(decision.resolved_scope.kind, "chapter")
        self.assertEqual(decision.resolved_scope.node_id, chapter["id"])

    def test_invalid_retrieval_scope_is_ignored_instead_of_failing_turn(self):
        model = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": "What are the hardest operational parts?",
                "scope_node_id": 999999,
                "reason": "The scope came from prior context.",
            }
        )

        decision = self.analyze(
            "What are the hardest operational parts?",
            self.state(previous_answer="Prior grounded answer."),
            model,
        )

        self.assertEqual(decision.route, "retrieval_qa")
        self.assertIsNone(decision.resolved_scope)

    def test_a_scope_outside_a_real_candidate_list_is_retried_three_times(self):
        """The model was shown choices and ignored them: try again."""

        model = FakeModel(
            {
                "route": "hierarchy_summary",
                "history_dependency": "dependent",
                "scope_node_id": 999999,
                "reason": "Invented scope.",
            }
        )

        with self.assertRaisesRegex(ConversationDecisionError, "after 3 attempts"):
            self.analyze(
                "Summarize the Low-Rank Factorization section.", self.state(), model
            )
        self.assertEqual(len(model.calls), 3)

    def test_a_question_matching_nothing_in_the_book_asks_rather_than_fails(self):
        """Regression: a 613-page book showed a raw internal error.

        Every one of its chapters was typed `other` and excluded from scope
        search, so "what sections are present in Chapter 1?" offered the model
        no candidates at all. It named one anyway, three times, and the reader
        was shown "selected scope is not canonical". Retrying cannot help when
        there was nothing to choose from.
        """

        model = FakeModel(
            {
                "route": "hierarchy_list",
                "history_dependency": "independent",
                "scope_node_id": 999999,
                "reason": "Invented scope.",
            }
        )

        decision = self.analyze("Summarize that section.", self.state(), model)

        self.assertEqual(decision.route, "clarify")
        self.assertIn("could not find", decision.clarification_question)
        # Asked once and answered; the retry loop is not the right tool here.
        self.assertEqual(len(model.calls), 1)

    def test_prior_transform_requires_an_answer(self):
        model = FakeModel(
            {
                "route": "prior_answer_transform",
                "history_dependency": "dependent",
                "reason": "Shorten the prior response.",
            }
        )

        with self.assertRaisesRegex(ConversationDecisionError, "after 3 attempts"):
            self.analyze("Make that shorter.", self.state(), model)

        decision = self.analyze(
            "Make that shorter.",
            self.state(previous_answer="Grounded answer."),
            model,
        )
        self.assertEqual(decision.route, "prior_answer_transform")

    def test_payload_contains_only_bounded_recent_context(self):
        messages = [
            ConversationMessage(
                role="user" if index % 2 == 0 else "assistant",
                content=f"message-{index}-" + "x" * 3000,
            )
            for index in range(10)
        ]
        model = FakeModel(
            {
                "route": "retrieval_qa",
                "history_dependency": "dependent",
                "standalone_query": "Explain the referenced concept.",
                "reason": "It depends on recent history.",
            }
        )

        self.analyze(
            "Explain it.",
            self.state(messages=messages, previous_answer="a" * 4000),
            model,
        )

        payload = json.loads(model.calls[0][0][1][1])
        self.assertEqual(len(payload["recent_messages"]), 6)
        self.assertNotIn("message-0-", str(payload))
        self.assertLessEqual(len(payload["previous_answer"]), 1601)

    def test_empty_question_fails_without_model_call(self):
        model = FakeModel()
        with self.assertRaisesRegex(ConversationDecisionError, "cannot be empty"):
            self.analyze(" ", self.state(), model)
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()


class ExplicitWebSearchRoutingTests(ConversationDecisionTests):
    """An asked-for web search is routed without consulting the model.

    Deterministic for the same reason the library listing is: the reader said
    what they wanted in so many words. Before this, "use web search to add
    more detail" reached the web only if retrieval happened to come up short
    first, so the one turn where the intent was explicit was the turn where it
    was least reliable.
    """

    def test_explicit_web_request_routes_to_external_qa(self):
        decision = self.analyze(
            "can you use web search to add more details on this part",
            self.state(previous_answer="Chapter 12 designs a chat system."),
            FailIfCalled(),
        )
        self.assertEqual(decision.route, "external_qa")
        self.assertEqual(decision.history_dependency, "dependent")
        self.assertIn("web search", decision.reason.casefold())

    def test_explicit_web_request_without_history_is_independent(self):
        decision = self.analyze(
            "search the web for current Kubernetes autoscaling guidance",
            self.state(),
            FailIfCalled(),
        )
        self.assertEqual(decision.route, "external_qa")
        self.assertEqual(decision.history_dependency, "independent")

    def test_book_topics_named_online_or_search_are_not_web_requests(self):
        """The route must not fire on 'online prediction' or 'search relevance'."""

        for question in (
            "Summarize Batch Prediction Versus Online Prediction in Chapter 7.",
            "How does the book describe research on search relevance?",
        ):
            with self.subTest(question=question):
                model = FakeModel(
                    ModelDecision(
                        route="retrieval_qa",
                        history_dependency="independent",
                        standalone_query=question,
                        reason="A book question.",
                    )
                )
                decision = self.analyze(question, self.state(), model)
                self.assertNotEqual(decision.route, "external_qa")
