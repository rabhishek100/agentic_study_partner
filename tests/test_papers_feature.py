import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID
from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from study.request import StudyRequest, parse_study_request

OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")


class ScientificPapersFeatureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        app.dependency_overrides[current_owner] = lambda: OWNER_ID

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_whole_paper_request_accepts_a_trailing_ui_mention(self) -> None:
        self.assertEqual(
            parse_study_request(
                "explain this paper @[Attention is All you Need]"
            ),
            StudyRequest(
                "summarize",
                "book",
                "",
                book_reference="Attention is All you Need",
            ),
        )

    def test_whole_document_summary_accepts_every_word_order(self) -> None:
        """Determiner, mention and noun, in the orders readers actually write.

        Enumerated rather than sampled because this failed twice in
        production, each time as a different unlisted phrasing: "the @[Title]
        paper" and then "@[Title] paper". Each was parsed as a *named* scope,
        sent to the hierarchy resolver as if the title were a section, matched
        nothing, and failed the turn with "hierarchy route requires a
        canonical scope".
        """

        for determiner in ("", "the ", "this ", "the whole "):
            for noun in ("", " paper", " document", " book", " pdf"):
                query = f"summarize {determiner}@[Attention Is All You Need]{noun}"
                with self.subTest(query=query):
                    self.assertEqual(
                        parse_study_request(query),
                        StudyRequest(
                            "summarize",
                            "book",
                            "",
                            book_reference="Attention Is All You Need",
                        ),
                    )

    def test_whole_document_summary_without_a_mention(self) -> None:
        """The reader is already looking at the document."""

        for query in (
            "summarize the paper",
            "summarize this document",
            "explain the whole pdf",
            "review the book",
        ):
            with self.subTest(query=query):
                parsed = parse_study_request(query)
                self.assertEqual(parsed.scope_kind, "book")
                self.assertEqual(parsed.scope_reference, "")
                self.assertIsNone(parsed.book_reference)

    def test_a_scoped_request_is_not_read_as_a_whole_document_one(self) -> None:
        """The distinction the whole feature rests on.

        Both kinds are `summarize`; the scope is what separates them. A
        widened pattern that swallowed these would silently turn "summarize
        chapter 1" into a summary of the entire book.
        """

        for query, kind, reference in (
            ("summarize chapter 1 of @[ML System Design]", "chapter", "1"),
            ("summarize the Introduction section", "named", "Introduction section"),
            ("summarize section 2 of the paper", "section", "2 of the paper"),
        ):
            with self.subTest(query=query):
                parsed = parse_study_request(query)
                self.assertEqual(parsed.scope_kind, kind)
                self.assertEqual(parsed.scope_reference, reference)

    async def test_papers_endpoint_returns_scientific_papers(self) -> None:
        papers_mock = [
            {
                "id": 42,
                "title": "Attention Is All You Need",
                "author": "Vaswani et al.",
                "page_count": 15,
                "ready_at": None,
                "document_type": "paper",
            }
        ]
        with (
            patch("api.main.database_connection") as open_connection,
            patch("api.main.list_books", return_value=papers_mock) as mock_list,
            patch("api.main.book_retrieval_completeness", return_value=(25, 25)),
        ):
            open_connection.return_value.__enter__.return_value = MagicMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/papers")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("books", payload)
        self.assertEqual(len(payload["books"]), 1)
        paper = payload["books"][0]
        self.assertEqual(paper["book_id"], 42)
        self.assertEqual(paper["title"], "Attention Is All You Need")
        self.assertEqual(paper["document_type"], "paper")
        self.assertTrue(paper["retrieval_complete"])
        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        self.assertEqual(kwargs.get("document_type"), "paper")

    async def test_books_endpoint_filters_by_document_type(self) -> None:
        books_mock = [
            {
                "id": 1,
                "title": "Designing Machine Learning Systems",
                "author": "Chip Huyen",
                "page_count": 350,
                "ready_at": None,
                "document_type": "book",
            }
        ]
        with (
            patch("api.main.database_connection") as open_connection,
            patch("api.main.list_books", return_value=books_mock) as mock_list,
            patch("api.main.book_retrieval_completeness", return_value=(100, 100)),
        ):
            open_connection.return_value.__enter__.return_value = MagicMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/books?document_type=book")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["books"]), 1)
        self.assertEqual(payload["books"][0]["document_type"], "book")

    async def test_create_paper_ingestion_job(self) -> None:
        job_id = UUID("33333333-3333-4333-8333-333333333333")
        created_job = MagicMock(
            id=job_id,
            status="awaiting_upload",
            storage_bucket="sources",
            storage_path="owner/job/attention.pdf",
            document_type="paper",
        )
        with (
            patch("api.ingestions.database_connection") as open_connection,
            patch("api.ingestions.create_job", return_value=(created_job, True)) as mock_create,
        ):
            open_connection.return_value.__enter__.return_value = MagicMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/ingestions",
                    headers={"Idempotency-Key": "44444444-4444-4444-8444-444444444444"},
                    json={
                        "original_filename": "attention_is_all_you_need.pdf",
                        "content_type": "application/pdf",
                        "content_length": 2100000,
                        "document_type": "paper",
                    },
                )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["job_id"], str(job_id))
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs.get("document_type"), "paper")

    async def test_conversations_endpoint_filters_by_document_type(self) -> None:
        now = datetime.now(timezone.utc)
        conversations_mock = [
            {
                "id": UUID("55555555-5555-5555-5555-555555555555"),
                "title": "Attention Paper Study",
                "book_ids": [543],
                "document_type": "paper",
                "retrieval_mode": "hybrid_rerank",
                "turn_count": 2,
                "created_at": now,
                "updated_at": now,
                "side_thread_count": 0,
            }
        ]
        with (
            patch("api.main.database_connection") as open_connection,
            patch("api.main.list_conversations", return_value=conversations_mock) as mock_list,
        ):
            open_connection.return_value.__enter__.return_value = MagicMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/conversations?document_type=paper")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["conversations"]), 1)
        self.assertEqual(payload["conversations"][0]["title"], "Attention Paper Study")
        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        self.assertEqual(kwargs.get("document_type"), "paper")


if __name__ == "__main__":
    unittest.main()
