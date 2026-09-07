"""Reading a chapter in the chat, rather than being told about it.

The properties under test are reproduction properties, not answer-quality
ones: this route calls no model, so what has to hold is that the segments a
reader receives are the blocks the parser stored, in order, unaltered, and
that paginating them loses nothing at any budget.
"""

import unittest
from dataclasses import replace
from unittest import mock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from api.auth import current_owner
from api.main import app
from parsing.models import (
    NON_CONTENT_CATEGORIES,
    ParsedBook,
    Section,
    TextBlock,
)
from storage.database import connection as database_connection, resolve_database_url
from storage.postgres import ingest_book
from study import analyze, query
from study.contracts import ScopeRef, TurnDecision
from study.conversation import (
    _hierarchy_query,
    execute_conversation_turn,
    new_conversation_state,
)
from study.content import load_scope_content
from study.reading import (
    build_reading_passage,
    installment,
    load_figure_details,
    node_ids,
)
from study.request import (
    UnsupportedStudyRequestError,
    parse_study_request,
)
from study.scope import resolve_book, resolve_chapter
from tests.fixtures import FILE_HASH, sample_book


def from_blocks(segments) -> list[tuple[int, str]]:
    """Every segment that came from a stored block, as (node, text).

    A node's own title heading is emitted before its first block, so the first
    segment of each node is the one segment that is *not* a block. Dropping it
    positionally rather than by matching on text is what makes this usable
    against a book whose section title also appears inside its prose.
    """

    seen: set[int] = set()
    reproduced = []
    for segment in segments:
        if segment.node_id not in seen:
            seen.add(segment.node_id)
            continue
        if segment.kind == "figure":
            continue
        reproduced.append((segment.node_id, segment.text))
    return reproduced


class VerbatimGrammarTests(unittest.TestCase):
    """The request forms, as a grammar rather than a list of sentences."""

    def assertReads(self, query: str, *, kind: str, reference: str) -> None:
        request = parse_study_request(query)
        self.assertEqual(request.intent, "read_verbatim", query)
        self.assertEqual(request.scope_kind, kind, query)
        self.assertEqual(request.scope_reference, reference, query)

    def test_a_marker_after_the_scope_asks_to_read_it(self) -> None:
        for query in (
            "read chapter 3 in full",
            "show me chapter 3 verbatim",
            "give me chapter 3 word for word",
            "open chapter 3 in the chat",
            "render chapter 3 unabridged",
        ):
            self.assertReads(query, kind="chapter", reference="3")

    def test_a_marker_before_the_scope_asks_the_same_thing(self) -> None:
        self.assertReads("verbatim chapter 3", kind="chapter", reference="3")
        self.assertReads("reader mode for chapter 3", kind="chapter", reference="3")

    def test_the_possessive_form_is_understood(self) -> None:
        for query in (
            "give me the full text of chapter 3",
            "show the complete text of chapter 3",
            "read the whole contents of chapter 3",
        ):
            self.assertReads(query, kind="chapter", reference="3")

    def test_every_scope_form_works_because_the_scope_grammar_is_reused(self) -> None:
        """The wrapper knows about markers; it knows nothing about chapters."""

        section = parse_study_request(
            "read the section Storage and Retrieval in full"
        )
        self.assertEqual(section.intent, "read_verbatim")
        self.assertEqual(section.scope_kind, "section")
        self.assertEqual(section.scope_reference, "Storage and Retrieval")

        inline_book = parse_study_request("read chapter 3 of ddia verbatim")
        self.assertEqual(inline_book.book_reference, "ddia")

        mention = parse_study_request(
            "make the @[Attention Is All You Need] paper a chat version"
        )
        self.assertEqual(mention.intent, "read_verbatim")
        self.assertEqual(mention.scope_kind, "book")
        self.assertEqual(mention.book_reference, "Attention Is All You Need")

        whole = parse_study_request("chat version of this paper")
        self.assertEqual(whole.intent, "read_verbatim")
        self.assertEqual(whole.scope_kind, "book")

    def test_the_book_may_be_named_before_the_chapter_or_after_it(self) -> None:
        """Both word orders, because readers write both.

        "read the @[Title] chapter 5 verbatim" failed in production: the whole
        phrase fell through to the named-scope catch-all, resolved to nothing,
        and the turn quietly became an ordinary retrieval question *about*
        chapter 5 instead of a request to read it.
        """

        for query in (
            "read chapter 5 of @[Scaler HLD] verbatim",
            "read the @[Scaler HLD] chapter 5 verbatim",
            "read @[Scaler HLD] chapter 5 in full",
            "give me the full text of @[Scaler HLD] chapter 5",
        ):
            request = parse_study_request(query)
            self.assertEqual(request.intent, "read_verbatim", query)
            self.assertEqual(request.scope_kind, "chapter", query)
            self.assertEqual(request.scope_reference, "5", query)
            self.assertEqual(request.book_reference, "Scaler HLD", query)

    def test_the_book_first_order_works_for_summaries_too(self) -> None:
        """The gap was in the shared scope grammar, not in this feature."""

        request = parse_study_request("summarize the @[Scaler HLD] chapter 5")
        self.assertEqual(request.intent, "summarize")
        self.assertEqual(request.scope_kind, "chapter")
        self.assertEqual(request.scope_reference, "5")
        self.assertEqual(request.book_reference, "Scaler HLD")

    def test_a_chapter_named_by_title_is_not_swallowed(self) -> None:
        """Only a numbered chapter takes the book-first form."""

        request = parse_study_request("summarize the caching chapter")
        self.assertEqual(request.scope_kind, "named")
        self.assertEqual(request.scope_reference, "caching chapter")

    def test_reading_aloud_is_a_different_feature_and_does_not_match(self) -> None:
        """Narration must not be swallowed by a marker set that got greedy."""

        with self.assertRaises(UnsupportedStudyRequestError):
            parse_study_request("read chapter 3 aloud")

    def test_the_existing_forms_are_unchanged(self) -> None:
        self.assertEqual(parse_study_request("summarize chapter 3").intent, "summarize")
        self.assertEqual(
            parse_study_request("list chapters").intent, "list_chapters"
        )
        self.assertEqual(
            parse_study_request("give me the chapter list").intent, "list_chapters"
        )
        self.assertEqual(
            parse_study_request("explain this paper").intent, "summarize"
        )

    def test_the_rendered_execution_sentence_parses_back_to_this_intent(self) -> None:
        """The router and the executor have to agree across a string.

        `_hierarchy_query` renders the decision as a sentence and
        `execute_query` re-parses it. A sentence that read as a summary
        request would quietly summarize what the reader asked to be shown.
        """

        for kind, path in (
            ("chapter", "Chapter 3. Storage and Retrieval"),
            ("section", "Chapter 3 > Hash Indexes"),
            ("book", "Attention Is All You Need"),
        ):
            decision = TurnDecision(
                route="verbatim_reading",
                history_dependency="independent",
                standalone_query=f"Read {path} verbatim.",
                resolved_scope=ScopeRef(
                    kind=kind,
                    book_id=1,
                    node_id=None if kind == "book" else 7,
                    display_path=path,
                    start_page=1,
                    end_page=9,
                ),
                reason="test",
            )
            round_tripped = parse_study_request(_hierarchy_query(decision))
            self.assertEqual(round_tripped.intent, "read_verbatim", path)
            # And it names the same scope the summary route would resolve, so
            # the two paths cannot drift into reading different chapters.
            summary_decision = decision.model_copy(
                update={"route": "hierarchy_summary"}
            )
            summarized = parse_study_request(_hierarchy_query(summary_decision))
            self.assertEqual(
                replace(round_tripped, intent="summarize"), summarized, path
            )


class ReadingPassageTests(unittest.TestCase):
    """Reproduction, over a real ingested book."""

    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_id = uuid4()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.connection.execute(
            """
            insert into auth.users (id, email, raw_user_meta_data)
            values (%s, %s, '{}'::jsonb)
            """,
            (self.owner_id, f"{self.owner_id}@test.local"),
        )
        self.book_id = ingest_book(
            self.connection,
            sample_book(),
            owner_id=self.owner_id,
            title="Sample Book",
            author=None,
            file_hash=FILE_HASH,
            page_count=5,
            parser_version="test-v1",
        )
        # A turn opens its own connection, so the book has to be visible from
        # outside this one.
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.execute(
            "delete from auth.users where id = %s", (self.owner_id,)
        )
        self.database_context.__exit__(None, None, None)

    def passage(self, reference: str = "1"):
        scope = resolve_chapter(
            self.connection,
            reference,
            owner_id=self.owner_id,
            book_id=self.book_id,
        )
        evidence = load_scope_content(
            self.connection, scope, owner_id=self.owner_id
        )
        return evidence, build_reading_passage(
            evidence,
            figure_details=load_figure_details(
                self.connection,
                list(node_ids(evidence)),
                owner_id=self.owner_id,
            ),
        )

    def test_every_stored_block_is_reproduced_in_order_and_unaltered(self) -> None:
        evidence, passage = self.passage()

        expected = [
            (block.node_id, block.readable_text.strip())
            for node in evidence.nodes
            for block in node.blocks
            if block.category not in NON_CONTENT_CATEGORIES
            and (block.readable_text or "").strip()
        ]
        self.assertEqual(from_blocks(passage.segments), expected)

    def test_a_figure_keeps_its_place_in_the_text_rather_than_a_gallery(self) -> None:
        _, passage = self.passage()

        figures = [s for s in passage.segments if s.kind == "figure"]
        self.assertEqual(len(figures), 1)
        self.assertIsNotNone(figures[0].figure)
        self.assertEqual(figures[0].figure.book_id, self.book_id)
        self.assertEqual(figures[0].figure.page, 3)
        # It follows its own heading rather than being appended at the end.
        self.assertEqual(passage.segments[figures[0].index - 1].kind, "heading")

    def test_a_table_carries_its_structure_and_a_flat_fallback(self) -> None:
        _, passage = self.passage()

        table = next(s for s in passage.segments if s.kind == "table")
        self.assertIn("<table>", table.html)
        self.assertEqual(table.text, "A")

    def test_a_heading_opens_each_node_at_its_own_depth(self) -> None:
        _, passage = self.passage()

        headings = [(s.text, s.level) for s in passage.segments if s.kind == "heading"]
        self.assertEqual(
            headings, [("Chapter 1", 1), ("Core idea", 2), ("Diagram", 3)]
        )

    def test_paginating_the_whole_passage_loses_and_repeats_nothing(self) -> None:
        """At any budget, the installments concatenate back to the passage."""

        _, passage = self.passage()

        for budget in (1, 5, 20, 6_000):
            collected = []
            offset = 0
            while True:
                page = installment(
                    passage.segments, offset=offset, max_characters=budget
                )
                # A budget smaller than one segment must still make progress,
                # or the reader stalls at a boundary they can never cross.
                self.assertGreater(len(page.segments), 0, budget)
                self.assertEqual(page.offset, offset, budget)
                collected.extend(page.segments)
                if page.next_offset is None:
                    break
                offset = page.next_offset
            self.assertEqual(
                [s.index for s in collected],
                [s.index for s in passage.segments],
                budget,
            )

    def test_the_route_runs_end_to_end_without_calling_a_model(self) -> None:
        """The central claim of this feature, asserted rather than described.

        Every model constructor on the path is replaced by one that raises, so
        a convenience call added later fails here instead of quietly making a
        verbatim passage cost money and stop being verbatim.
        """

        def refuse(*args, **kwargs):
            raise AssertionError("a model was constructed on the verbatim route")

        with (
            mock.patch.object(query, "openrouter_model", refuse),
            mock.patch.object(query, "control_model", refuse),
            mock.patch.object(analyze, "_openrouter_model", refuse),
        ):
            result, _ = execute_conversation_turn(
                "read chapter 1 in full",
                new_conversation_state(book_ids=[self.book_id]),
                owner_id=self.owner_id,
            )

        self.assertEqual(result.route, "verbatim_reading")
        self.assertEqual(result.outcome, "answer")
        self.assertIsNotNone(result.reading)
        self.assertEqual(result.reading.display_path, "Chapter 1")
        # A reproduction asserts nothing, so it grounds nothing.
        self.assertEqual(result.citations, [])
        self.assertEqual(result.evidence, [])
        # And the conversation can still be followed up on: the scope it read
        # is the scope a later "summarize this" resolves against.
        self.assertIsNotNone(result.resolved_scope)

    def test_a_whole_document_reads_from_the_top(self) -> None:
        scope = resolve_book(
            self.connection, None, owner_id=self.owner_id, book_id=self.book_id
        )
        evidence = load_scope_content(
            self.connection, scope, owner_id=self.owner_id
        )
        passage = build_reading_passage(evidence)

        self.assertEqual(passage.reference().kind, "book")
        self.assertGreater(passage.reference().total_segments, 0)


def typeset_book() -> ParsedBook:
    """A page shaped like the real corpus: titles, a list, a formula, a stamp.

    The margin stamp is the arXiv identifier printed down the side of a
    preprint's first page. The layout parser decomposes rotated text into one
    block per glyph, and 5 181 such blocks exist across this library.
    """

    stamp = [
        TextBlock(text=glyph, category="UncategorizedText", page=1)
        for glyph in "5202:viXra"
    ]
    return ParsedBook(
        source="sources/papers/typeset.pdf",
        toc=[(1, "Full paper", 1)],
        sections=[
            Section(
                path=["Full paper"],
                level=1,
                start_page=1,
                end_page=1,
                texts=[
                    *stamp,
                    TextBlock(text="A Study of Things", category="Title", page=1),
                    TextBlock(
                        text="We argue that things matter.",
                        category="NarrativeText",
                        page=1,
                    ),
                    TextBlock(text="1 Introduction", category="Title", page=1),
                    TextBlock(text="WD1 A thing is a thing.", category="ListItem", page=1),
                    TextBlock(text="WD2 Anything else is not.", category="ListItem", page=1),
                    TextBlock(text="E = mc^2", category="Formula", page=1),
                    TextBlock(text="Figure 1: a thing.", category="FigureCaption", page=1),
                    TextBlock(text="7", category="DetectedFooter", page=1),
                ],
            )
        ],
    )


class TypesetPassageTests(unittest.TestCase):
    """How the parser's own categories reach the page."""

    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_id = uuid4()
        self.database_context = database_connection(self.database_url)
        self.connection = self.database_context.__enter__()
        self.connection.execute(
            """
            insert into auth.users (id, email, raw_user_meta_data)
            values (%s, %s, '{}'::jsonb)
            """,
            (self.owner_id, f"{self.owner_id}@test.local"),
        )
        self.book_id = ingest_book(
            self.connection,
            typeset_book(),
            owner_id=self.owner_id,
            title="A Study of Things",
            author=None,
            file_hash="c" * 64,
            page_count=1,
            parser_version="test-v1",
        )
        scope = resolve_book(
            self.connection, None, owner_id=self.owner_id, book_id=self.book_id
        )
        self.passage = build_reading_passage(
            load_scope_content(self.connection, scope, owner_id=self.owner_id)
        )

    def tearDown(self) -> None:
        self.connection.execute(
            "delete from auth.users where id = %s", (self.owner_id,)
        )
        self.database_context.__exit__(None, None, None)

    def kinds(self) -> list[tuple[str, str]]:
        return [(s.kind, s.text or "") for s in self.passage.segments]

    def test_the_parsers_own_categories_decide_how_a_block_is_set(self) -> None:
        self.assertEqual(
            self.kinds(),
            [
                ("heading", "Full paper"),
                ("heading", "A Study of Things"),
                ("text", "We argue that things matter."),
                ("heading", "1 Introduction"),
                ("list_item", "WD1 A thing is a thing."),
                ("list_item", "WD2 Anything else is not."),
                ("formula", "E = mc^2"),
                ("caption", "Figure 1: a thing."),
            ],
        )

    def test_a_title_inside_a_node_sits_one_level_under_it(self) -> None:
        levels = [s.level for s in self.passage.segments if s.kind == "heading"]
        self.assertEqual(levels, [1, 2, 2])

    def test_a_rotated_margin_stamp_is_omitted_rather_than_read_out(self) -> None:
        """Ten one-letter paragraphs at the top of a paper are not the paper."""

        for _, text in self.kinds():
            self.assertGreater(len(text), 1, text)
        # The ten stamp glyphs plus the page-number footer.
        self.assertEqual(self.passage.omitted_block_count, 11)

    def test_a_lone_short_block_is_kept_because_it_may_be_real_text(self) -> None:
        """The signature is the run, not the length of any one block."""

        from study.reading import MARGINALIA_RUN_LENGTH, _marginalia

        class Block:
            def __init__(self, id, text, page=1):
                self.id, self.page_number = id, page
                self.block_type, self.readable_text = "text", text

        short_run = [
            Block(index, "x") for index in range(MARGINALIA_RUN_LENGTH - 1)
        ]
        self.assertEqual(_marginalia(short_run), set())
        self.assertEqual(
            _marginalia([*short_run, Block(99, "y")]),
            {0, 1, 2, 99},
        )

    def test_a_run_broken_by_a_page_turn_is_two_runs(self) -> None:
        from study.reading import _marginalia

        class Block:
            def __init__(self, id, text, page):
                self.id, self.page_number = id, page
                self.block_type, self.readable_text = "text", text

        split = [Block(0, "a", 1), Block(1, "b", 1), Block(2, "c", 2), Block(3, "d", 2)]
        self.assertEqual(_marginalia(split), set())


class PassageEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_id = uuid4()
        self.stranger_id = uuid4()
        with database_connection(self.database_url) as connection:
            for owner in (self.owner_id, self.stranger_id):
                connection.execute(
                    """
                    insert into auth.users (id, email, raw_user_meta_data)
                    values (%s, %s, '{}'::jsonb)
                    """,
                    (owner, f"{owner}@test.local"),
                )
            self.book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Sample Book",
                author=None,
                file_hash=FILE_HASH,
                page_count=5,
                parser_version="test-v1",
            )
            self.other_book_id = ingest_book(
                connection,
                sample_book(),
                owner_id=self.owner_id,
                title="Another Book",
                author=None,
                file_hash="b" * 64,
                page_count=5,
                parser_version="test-v1",
            )
            self.node_id = resolve_chapter(
                connection, "1", owner_id=self.owner_id, book_id=self.book_id
            ).root_node_id

        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        app.dependency_overrides[current_owner] = lambda: self.owner_id

    async def asyncTearDown(self) -> None:
        app.dependency_overrides.clear()
        await self.client.aclose()
        with database_connection(self.database_url) as connection:
            connection.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner_id, self.stranger_id],),
            )

    async def test_the_first_installment_describes_the_whole_passage(self) -> None:
        response = await self.client.get(
            f"/api/books/{self.book_id}/passage",
            params={"node_id": self.node_id, "max_characters": 20},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["offset"], 0)
        self.assertEqual(body["reading"]["display_path"], "Chapter 1")
        self.assertEqual(body["reading"]["book_title"], "Sample Book")
        # The reference counts the whole scope even though this page is one
        # segment: it is what the interface shows progress against.
        self.assertGreater(body["reading"]["total_segments"], len(body["segments"]))
        self.assertIsNotNone(body["next_offset"])

    async def test_following_next_offset_reaches_the_end(self) -> None:
        segments = []
        offset = 0
        while True:
            response = await self.client.get(
                f"/api/books/{self.book_id}/passage",
                params={
                    "node_id": self.node_id,
                    "offset": offset,
                    "max_characters": 20,
                },
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            segments.extend(body["segments"])
            if body["next_offset"] is None:
                break
            offset = body["next_offset"]

        self.assertEqual(
            [segment["index"] for segment in segments],
            list(range(body["reading"]["total_segments"])),
        )

    async def test_omitting_the_node_reads_the_whole_document(self) -> None:
        response = await self.client.get(f"/api/books/{self.book_id}/passage")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reading"]["kind"], "book")

    async def test_a_node_from_another_book_is_not_served_under_this_one(self) -> None:
        """A stale link must not quietly read out of a different book."""

        response = await self.client.get(
            f"/api/books/{self.other_book_id}/passage",
            params={"node_id": self.node_id},
        )

        self.assertEqual(response.status_code, 404)

    async def test_another_owners_book_is_not_readable(self) -> None:
        app.dependency_overrides[current_owner] = lambda: self.stranger_id

        response = await self.client.get(f"/api/books/{self.book_id}/passage")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
