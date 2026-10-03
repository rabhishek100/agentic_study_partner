"""Canonical IDs must survive planning -> execution without reparsing titles."""
import unittest
from uuid import uuid4

from storage.database import connection
from storage.postgres import ingest_book
from study.contracts import ScopeRef
from study.query import _resolve_hierarchy_request, QueryExecutionError
from study.scope import ScopeNotFoundError
from study.contracts import TurnDecision
from study.conversation import execute_decision, new_conversation_state
from tests.test_query_routing import CitationSummaryModel
from tests.fixtures import sample_book
from tests.postgres import PostgresOwnerMixin


class ResolvedScopeExecutionTests(PostgresOwnerMixin,unittest.TestCase):
    def setUp(self):
        self.setUpPostgresOwner()
        with connection(self.database_url) as db:
            self.book=ingest_book(db,sample_book(),owner_id=self.owner_id,title="Scope fixture",author="Fixture",
                file_hash=uuid4().hex*2,page_count=20,parser_version="fixture")
            self.other=ingest_book(db,sample_book(),owner_id=self.owner_id,title="Other fixture",author="Fixture",
                file_hash=uuid4().hex*2,page_count=20,parser_version="fixture")
            self.section=db.execute("select id from nodes where book_id=%s and toc_index=1",(self.book,)).fetchone()["id"]
        self.scope=ScopeRef(kind="section",book_id=self.book,node_id=self.section,
            display_path="1. Foundations :: Core idea",start_page=2,end_page=2)

    def tearDown(self):
        self.tearDownPostgresOwner()

    def resolve(self,scope=None,book_ids=None,owner=None):
        return _resolve_hierarchy_request("Summarize 1. Foundations :: Core idea.",
            database_url=self.database_url,owner_id=owner or self.owner_id,book_id=None,
            book_ids=book_ids or [self.book],planned_scope=scope or self.scope)

    def test_section_path_can_be_display_only_and_full_subtree_is_retained(self):
        request,scope=self.resolve()
        self.assertEqual(request.intent,"summarize")
        self.assertEqual(scope.root_node_id,self.section)
        self.assertEqual(len(scope.nodes),2)

    def test_scope_from_unselected_book_is_rejected(self):
        with self.assertRaises(QueryExecutionError):self.resolve(book_ids=[self.other])

    def test_node_from_different_book_cannot_be_relabelled(self):
        wrong=self.scope.model_copy(update={"book_id":self.other})
        with self.assertRaises(QueryExecutionError):self.resolve(scope=wrong,book_ids=[self.other])

    def test_foreign_owner_cannot_execute_saved_scope(self):
        with self.assertRaises(ScopeNotFoundError):self.resolve(owner=str(uuid4()))

    def test_planned_section_survives_conversation_execution(self):
        decision = TurnDecision(route="hierarchy_summary", history_dependency="independent",
            standalone_query="Summarize the core idea.", resolved_scope=self.scope,
            reason="The reader selected this canonical section.")
        result = execute_decision("Summarize the core idea.", decision,
            new_conversation_state(book_ids=[self.book], conversation_id="scope-fixture"),
            database_url=self.database_url, owner_id=self.owner_id,
            retrieval_mode="bm25", model=CitationSummaryModel())
        self.assertEqual(result.route,"hierarchy_summary")
        self.assertEqual(result.resolved_scope.node_id,self.section)

    def test_preface_chapter_request_keeps_canonical_subtree(self):
        # The existing title resolver treats top-level front matter as a
        # chapter request; resolving its ID must not reject that valid plan.
        from study.scope import resolve_chapter
        from study.query import _scope_ref
        with connection(self.database_url) as db:
            chapter = db.execute("select id from nodes where book_id=%s and toc_index=0",
                (self.book,)).fetchone()["id"]
            db.execute("update nodes set node_type='front_matter', title='Preface' where id=%s", (chapter,))
            planned = _scope_ref(resolve_chapter(db,"Preface",owner_id=self.owner_id,book_id=self.book))
        _, scope = _resolve_hierarchy_request("Summarize Chapter Preface.", database_url=self.database_url,
            owner_id=self.owner_id, book_id=self.book, planned_scope=planned)
        self.assertEqual(scope.root_node_id, chapter)
        self.assertGreater(len(scope.nodes), 1)
