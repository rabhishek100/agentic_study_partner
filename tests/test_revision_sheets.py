"""Artifact contracts, page fit, source boundaries, and transactional lifecycle."""

import json
import asyncio
import math
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import pymupdf
from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from revision_sheets import store
from revision_sheets.contracts import RevisionError, ScopeRequest, Sheet
from revision_sheets.generate import LAYOUT_COMBINATIONS, SCHEMA_ATTEMPTS
from revision_sheets.html_render import max_pages
from revision_sheets.generate import Draft, generate
from revision_sheets.render import render_pdf, diagram_layout
from revision_sheets.source import Source, load_source
from revision_sheets.validate import validate_sheet, resolve_disposition_concepts
from revision_sheets.worker import RevisionWorker
from storage.database import connection
from storage.postgres import ingest_book
from tests.fixtures import sample_book
from tests.postgres import PostgresOwnerMixin
from tests.test_deck_paper_scope import ingest_paper


def fixture():
    return Sheet.model_validate_json(Path("frontend/tests/fixtures/revision_sheet.json").read_text())


def source_fixture():
    return Source(ScopeRequest(scope_kind="chapter", book_id=1, chapter_node_id=1),
        "Illustrative renderer fixture", "Chapter 1", "a" * 64, "[N1:P1] Supported source fixture.",
        {"[N1:P1]": {"section_number": "1", "section_title": "Reliable event processing", "page": 1}},
        {"u1": {"[N1:P1]"}}, [])


class SequenceModel:
    def __init__(self, *sheets):
        self.sheets = list(sheets)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return Draft(sheet=self.sheets.pop(0), missing_evidence="")


class ContractTests(unittest.TestCase):
    def setUp(self):
        from revision_sheets.review import Inventory, EvidenceConcept, Review, Score, Coverage
        concept = EvidenceConcept(id="fixture", label="Supported fixture", explanation="Supported fixture", citations=["[N1:P1]"], importance="essential")
        inventory = Inventory(concepts=[concept], contradictions=[], source_coverage=[])
        score = Score(score=4, rationale="Fixture review")
        review = Review(beauty=score, presentation=score, concept_coverage=score, conciseness=score,
            coverage=[Coverage(concept_id="fixture", item_ids=[fixture().central_idea.id], status="covered", reason="Fixture")],
            unsupported_claims=[], unresolved_contradictions=[], revision_instructions=[])
        for target, value in (("make_inventory", inventory), ("judge_sheet", review)):
            patcher = patch("revision_sheets.generate." + target, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)

    def test_diagram_number_labels_do_not_overlap_on_branching_graph(self):
        from revision_sheets.contracts import DiagramEdge
        sheet = fixture()
        nodes = sheet.diagram.nodes
        sheet.diagram.edges.extend([
            DiagramEdge(id="branch", source=nodes[0].id, target=nodes[3].id, label="Branches", citations=["[N1:P1]"]),
            DiagramEdge(id="return", source=nodes[3].id, target=nodes[1].id, label="Returns", citations=["[N1:P1]"]),
        ])
        layout = diagram_layout(sheet)
        for i, edge in enumerate(layout["edges"]):
            for earlier in layout["edges"][:i]:
                self.assertGreaterEqual(math.dist(edge["label"], earlier["label"]), 16)

    def test_inventory_aliases_resolve_only_through_existing_rendered_items(self):
        sheet = fixture()
        concept = sheet.essential_concepts[0]
        sheet.source_dispositions[0].item_ids = [concept.id, "unknown"]
        self.assertEqual(resolve_disposition_concepts(sheet), 1)
        self.assertEqual(sheet.source_dispositions[0].item_ids, concept.item_ids + ["unknown"])
        with self.assertRaises(RevisionError):
            validate_sheet(sheet, allowed={"[N1:P1]"}, units={"u1": {"[N1:P1]"}}, figure_ids=set(), scope_kind="chapter")

    def test_pdf_is_single_a4_page_and_preserves_text_at_readable_size(self):
        s = source_fixture()
        data = render_pdf(fixture(), source_title=s.title, scope_title=s.scope_title, references=s.references)
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            self.assertEqual(len(pdf), 1)
            self.assertAlmostEqual(pdf[0].rect.width, 595.276, places=2)
            self.assertAlmostEqual(pdf[0].rect.height, 841.89, places=2)
            text = pdf[0].get_text()
            self.assertIn("Idempotency", text)
            self.assertIn("hot partition remains a bottleneck", text)
            self.assertNotIn("[N1:P1]", text)
            spans = [span for b in pdf[0].get_text("dict")["blocks"] if "lines" in b for line in b["lines"] for span in line["spans"]]
            self.assertGreaterEqual(min(s["size"] for s in spans), 7.99)
            for span in spans:
                self.assertTrue(pdf[0].rect.contains(pymupdf.Rect(span["bbox"])))

    def test_overflow_is_rejected_instead_of_scaling_or_clipping(self):
        sheet = fixture()
        sheet.essential_notes[0].text = "Long evidence with essential qualifications. " * 70
        s = source_fixture()
        with self.assertRaisesRegex(RevisionError, "Essential concepts"):
            render_pdf(sheet, source_title=s.title, scope_title=s.scope_title, references=s.references)

    def test_wrong_sources_broken_graph_and_missing_ledger_are_rejected(self):
        for mutate in (
            lambda s: setattr(s.central_idea, "citations", ["[N9:P99]"]),
            lambda s: setattr(s.diagram.edges[0], "target", "missing"),
            lambda s: setattr(s, "source_dispositions", []),
            lambda s: setattr(s.diagram, "source_figure_ids", [999]),
            lambda s: setattr(s.essential_concepts[0], "item_ids", ["missing"]),
        ):
            sheet = fixture(); mutate(sheet)
            with self.assertRaises(RevisionError):
                validate_sheet(sheet, allowed={"[N1:P1]"}, units={"u1": {"[N1:P1]"}}, figure_ids=set(), scope_kind="chapter")

    def test_content_repairs_are_bounded_and_one_layout_repair_is_allowed(self):
        """Bounded, but not to a single attempt.

        The model is nondeterministic, and one draft the schema rejects used to
        end a job that costs real money to reach. It now gets
        `SCHEMA_ATTEMPTS` tries; what has not changed is that the budget is
        finite, so a model that never produces valid output cannot loop.
        """

        bad = fixture(); bad.central_idea.citations = ["[N9:P99]"]
        model = SequenceModel(bad, fixture())
        sheet, pdf, provenance = generate(source_fixture(), model=model, images=([], [], []), review_clients=(None, None, None))
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(provenance["content_repairs"], 1)
        self.assertTrue(pdf.startswith(b"%PDF"))
        # A draft that is still wrong on the last permitted attempt fails, and
        # the failure names what was wrong instead of only that something was.
        model = SequenceModel(*[bad] * (SCHEMA_ATTEMPTS + 1))
        with self.assertRaises(RevisionError) as caught:
            generate(source_fixture(), model=model, images=([], [], []), review_clients=(None, None, None))
        self.assertEqual(len(model.calls), SCHEMA_ATTEMPTS + 1)
        self.assertIn("citations must be exact supplied markers", str(caught.exception))
        model = SequenceModel(fixture(), fixture())
        from revision_sheets import generate as generation_module
        real_render = generation_module.render_html_pdf
        calls = []
        # Every configuration of the layout search has to fail before the fit
        # repair is reached. Read from the search itself: this number has
        # drifted twice — once when the sheet gained pages and once when page
        # one gained note slots — each time silently turning this into a test
        # that the search succeeds on its Nth try.
        sweep = (max_pages() - 1) * 2 * len(LAYOUT_COMBINATIONS)

        def fail_once(*args, **kwargs):
            calls.append(True)
            if len(calls) <= sweep:
                raise RevisionError("page_overflow", "Shorten the diagram.")
            return real_render(*args, **kwargs)
        with patch.object(generation_module, "render_html_pdf", side_effect=fail_once):
            _, _, provenance = generate(source_fixture(), model=model, images=([], [], []), review_clients=(None, None, None))
        self.assertEqual(provenance["fit_repairs"], 1)
        self.assertEqual(len(model.calls), 2)

    def test_context_overflow_never_calls_model(self):
        model = SequenceModel(fixture())
        with patch.dict("os.environ", {"REVISION_CONTEXT_WINDOW_TOKENS": "1000"}):
            with self.assertRaisesRegex(RevisionError, "No evidence was truncated"):
                generate(source_fixture(), model=model, images=([], [], []), review_clients=(None, None, None))
        self.assertEqual(model.calls, [])


class PersistenceTests(PostgresOwnerMixin, unittest.TestCase):
    def setUp(self):
        self.setUpPostgresOwner()
        with connection(self.database_url) as db:
            self.book = ingest_book(db, sample_book(), owner_id=self.owner_id, title="Revision test book", author="Test",
                                   file_hash=uuid4().hex * 2, page_count=20, parser_version="test")
            db.execute("update books set status='ready', ready_at=now() where id=%s", (self.book,))
            self.chapter = db.execute("select id from nodes where book_id=%s and node_type='chapter' order by toc_index limit 1", (self.book,)).fetchone()["id"]
            self.request = ScopeRequest(scope_kind="chapter", book_id=self.book, chapter_node_id=self.chapter)

    def tearDown(self):
        self.tearDownPostgresOwner()

    def load(self, db):
        return load_source(db, owner_id=self.owner_id, request=self.request)

    def queue(self, *, regenerate=False):
        with connection(self.database_url) as db:
            return store.enqueue(db, self.owner_id, self.load(db), str(uuid4()), regenerate=regenerate)["job"]

    def test_scope_is_complete_and_fingerprint_tracks_content_without_retrieval(self):
        with connection(self.database_url) as db:
            source = self.load(db)
            self.assertTrue(source.units)
            before = source.fingerprint
            db.execute("update content_blocks set text_content=text_content || ' Additional canonical evidence.' where book_id=%s and node_id=%s and block_type='text'", (self.book, self.chapter))
            self.assertNotEqual(self.load(db).fingerprint, before)
            with self.assertRaises(RevisionError):
                load_source(db, owner_id=str(uuid4()), request=self.request)
            paper_id = ingest_paper(db, owner_id=self.owner_id, file_hash=uuid4().hex * 2)
            db.execute("update books set status='ready' where id=%s", (paper_id,))
            paper = load_source(db, owner_id=self.owner_id, request=ScopeRequest(scope_kind="paper", book_id=paper_id))
            self.assertEqual(len(paper.units), 4)
            self.assertIn("Results", paper.text)
            with self.assertRaises(RevisionError):
                load_source(db, owner_id=self.owner_id, request=ScopeRequest(scope_kind="paper", book_id=self.book))

    def test_duplicates_cancellation_and_cross_owner_reads(self):
        with connection(self.database_url) as db:
            s = self.load(db)
            first = store.enqueue(db, self.owner_id, s, "same")["job"]
            self.assertEqual(first["id"], store.enqueue(db, self.owner_id, s, "same")["job"]["id"])
            self.assertEqual(first["id"], store.enqueue(db, self.owner_id, s, "different")["job"]["id"])
            with self.assertRaises(RevisionError):
                store.read_job(db, str(uuid4()), first["id"])
            cancelled = store.cancel(db, self.owner_id, first["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(store.enqueue(db, self.owner_id, s, "different")["job"]["id"], first["id"])
            with self.assertRaises(RevisionError):
                store.enqueue(db, self.owner_id, s, "different", regenerate=True)
            self.assertIsNone(store.claim(db, "test", owner=self.owner_id))

    def test_publish_reopen_and_failed_regeneration_preserve_previous_version(self):
        self.queue()
        with connection(self.database_url) as db:
            job = store.claim(db, "revision-test", owner=self.owner_id)
            s = self.load(db)
        # Lifecycle test uses an illustrative artifact; semantic source quality
        # is assessed independently in evaluation, never inferred from this stub.
        data = render_pdf(fixture(), source_title="Fixture", scope_title="Fixture", references=source_fixture().references)
        with connection(self.database_url) as db:
            store.publish(db, job, "revision-test", s, fixture(), data, {"fixture": True})
        with connection(self.database_url) as db:
            saved = store.read_sheet(db, self.owner_id, job["id"], pdf=True)
            self.assertEqual(bytes(saved["pdf_bytes"]), data)
            self.assertEqual(store.enqueue(db, self.owner_id, self.load(db), "reopen")["sheet"]["id"], saved["id"])
            with self.assertRaises(RevisionError):
                store.read_sheet(db, str(uuid4()), saved["id"])
        self.queue(regenerate=True)
        with connection(self.database_url) as db:
            newer = store.claim(db, "revision-test", owner=self.owner_id)
            store.cancel(db, self.owner_id, newer["id"])
            with self.assertRaises(RevisionError):
                store.publish(db, newer, "revision-test", self.load(db), fixture(), data, {})
            store.finish_failure(db, newer["id"], "revision-test", "cancelled", "Cancelled")
            self.assertEqual(store.list_sheets(db, self.owner_id, "book")["sheets"][0]["id"], saved["id"])
            self.assertEqual(store.enqueue(db, self.owner_id, self.load(db), "reopen")["job"]["sheet_id"], saved["id"])

        async def check_api_access():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                try:
                    app.dependency_overrides[current_owner] = lambda: self.owner_id
                    response = await client.get(f"/api/revision-sheets/{saved['id']}/pdf")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, data)
                    app.dependency_overrides[current_owner] = lambda: uuid4()
                    self.assertEqual((await client.get(f"/api/revision-sheets/{saved['id']}/pdf")).status_code, 404)
                    self.assertEqual((await client.get(f"/api/revision-sheets/{saved['id']}")).status_code, 404)
                finally:
                    app.dependency_overrides.pop(current_owner, None)
        asyncio.run(check_api_access())

    def test_a_spending_refusal_is_recorded_as_its_own_outcome(self):
        """Not "generation failed": the worker and the connection were fine."""

        from revision_sheets.worker import RevisionWorker

        self.queue()
        with connection(self.database_url) as db:
            job = store.claim(db, "revision-worker-test", owner=self.owner_id)
        worker = RevisionWorker(worker_id="revision-worker-test", database_url=self.database_url)

        class Refused(Exception):
            status_code = 403

        refusal = Refused(
            "Error code: 403 - {'error': {'message': 'Key limit exceeded "
            "(monthly limit). Manage it using https://openrouter.ai/keys/abc'}}"
        )
        with patch("revision_sheets.worker.generate", side_effect=refusal):
            worker.process(job)

        with connection(self.database_url) as db:
            failed = store.read_job(db, self.owner_id, job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "provider_quota_exhausted")
        self.assertIn("Key limit exceeded", failed["error_detail"])
        # The two things that were working are not blamed, and the reader is
        # told what to change instead of being invited to retry.
        self.assertNotIn("worker/provider connection", failed["error_detail"])
        self.assertIn("Raise the limit or add credit", failed["error_detail"])

    def test_an_ordinary_failure_still_reads_as_one(self):
        self.queue()
        with connection(self.database_url) as db:
            job = store.claim(db, "revision-worker-test", owner=self.owner_id)
        worker = RevisionWorker(worker_id="revision-worker-test", database_url=self.database_url)

        with patch("revision_sheets.worker.generate", side_effect=RuntimeError("boom")):
            worker.process(job)

        with connection(self.database_url) as db:
            failed = store.read_job(db, self.owner_id, job["id"])
        self.assertEqual(failed["error_code"], "generation_failed")

    def test_worker_publishes_atomically_and_rejects_changed_source(self):
        self.queue()
        with connection(self.database_url) as db:
            job = store.claim(db, "revision-worker-test", owner=self.owner_id)
        worker = RevisionWorker(worker_id="revision-worker-test", database_url=self.database_url)
        data = render_pdf(fixture(), source_title="Fixture", scope_title="Fixture", references=source_fixture().references)
        with patch("revision_sheets.worker.generate", return_value=(fixture(), data, {"fixture": True})) as model:
            worker.process(job)
            self.assertEqual(model.call_count, 1)
        with connection(self.database_url) as db:
            self.assertEqual(store.read_job(db, self.owner_id, job["id"])["status"], "ready")
            self.assertEqual(bytes(store.read_sheet(db, self.owner_id, job["id"], pdf=True)["pdf_bytes"]), data)
        self.queue(regenerate=True)
        with connection(self.database_url) as db:
            newer = store.claim(db, "revision-worker-test", owner=self.owner_id)
        def change_source(*args, **kwargs):
            with connection(self.database_url) as db:
                db.execute("update books set title='Changed during generation' where id=%s", (self.book,))
            return fixture(), data, {}
        with patch("revision_sheets.worker.generate", side_effect=change_source):
            worker.process(newer)
        with connection(self.database_url) as db:
            self.assertEqual(store.read_job(db, self.owner_id, newer["id"])["error_code"], "source_changed")
            self.assertEqual(len(store.list_sheets(db, self.owner_id, "book")["sheets"]), 1)

    def test_database_policies_hide_other_owners_and_prevent_browser_writes(self):
        from psycopg.errors import InsufficientPrivilege
        self.queue()
        with connection(self.database_url) as db:
            db.execute("set local role authenticated")
            db.execute("select set_config('request.jwt.claim.sub', %s, true)", (self.owner_id,))
            self.assertEqual(db.execute("select count(*) as n from revision_sheet_jobs").fetchone()["n"], 1)
            try:
                with db.transaction():
                    changed = db.execute("update revision_sheet_jobs set status='cancelled'").rowcount
                    self.assertEqual(changed, 0)
            except InsufficientPrivilege:
                pass
            db.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(uuid4()),))
            self.assertEqual(db.execute("select count(*) as n from revision_sheet_jobs").fetchone()["n"], 0)

    def test_expired_lease_cannot_publish_and_is_explicitly_retryable(self):
        self.queue()
        with connection(self.database_url) as db:
            job = store.claim(db, "revision-test", owner=self.owner_id)
            db.execute("update revision_sheet_jobs set lease_expires_at=now()-interval '1 minute' where id=%s", (job["id"],))
            self.assertFalse(store.heartbeat(db, job["id"], "revision-test"))
            store.recover(db)
            self.assertEqual(store.read_job(db, self.owner_id, job["id"])["status"], "failed")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_and_invalid_scope_requests(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            self.assertEqual((await client.get("/api/revision-sheets")).status_code, 401)
            app.dependency_overrides[current_owner] = lambda: uuid4()
            try:
                for body in [{"scope_kind": "chapter", "book_id": 1}, {"scope_kind": "paper", "book_id": 1, "chapter_node_id": 2}]:
                    response = await client.post("/api/revision-sheets", json=body, headers={"Idempotency-Key": "test"})
                    self.assertEqual(response.status_code, 422)
            finally:
                app.dependency_overrides.pop(current_owner, None)
