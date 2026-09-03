"""Cross-user isolation for canonical content, retrieval, and jobs.

Two owners each store a book, then every owner-scoped entry point is asked for
the other owner's data. Application filters and Postgres RLS are tested
separately because either one alone would be a single point of failure.
"""

import unittest
from uuid import UUID, uuid4

from psycopg import errors as postgres_errors

from retrieval.models import ChunkingConfig
from retrieval.postgres import rebuild, search
from storage.database import connection, resolve_database_url
from storage.postgres import (
    list_books,
    mark_book_ready,
    ingest_book,
    ready_book,
    restore_book,
)
from storage.conversations import (
    append_turn,
    create_conversation,
    delete_conversation,
    list_conversations,
    load_conversation,
    load_turns,
)
from tests.fixtures import sample_book


class TwoOwnerFixture:
    """One book per owner, created with the same content and file hash."""

    def provision(self) -> None:
        self.database_url = resolve_database_url()
        self.owner_a = str(uuid4())
        self.owner_b = str(uuid4())
        self.books: dict[str, int] = {}

        with connection(self.database_url) as database:
            for owner in (self.owner_a, self.owner_b):
                database.execute(
                    """
                    insert into auth.users (id, email, raw_user_meta_data)
                    values (%s, %s, '{}'::jsonb)
                    """,
                    (owner, f"{owner}@test.local"),
                )
                # The same file hash on purpose: one owner's library must not
                # reveal that another owner uploaded the same book.
                self.books[owner] = ingest_book(
                    database,
                    sample_book(),
                    owner_id=owner,
                    title="Shared Title",
                    author=None,
                    file_hash="b" * 64,
                    page_count=5,
                    parser_version="test-parser",
                )

    def provision_conversations(self) -> None:
        """One conversation with one turn per owner."""

        self.conversations: dict[str, str] = {}
        with connection(self.database_url) as database:
            for owner in (self.owner_a, self.owner_b):
                conversation = create_conversation(
                    database,
                    owner_id=owner,
                    book_ids=[self.books[owner]],
                    retrieval_mode="hybrid",
                    title=f"{owner} private title",
                )
                self.conversations[owner] = str(conversation["id"])
                append_turn(
                    database,
                    conversation["id"],
                    owner_id=owner,
                    question=f"{owner} private question",
                    answer=f"{owner} private answer",
                    result={"answer": f"{owner} private answer"},
                    state={"conversation_id": str(conversation["id"])},
                )

    def discard(self) -> None:
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)",
                ([self.owner_a, self.owner_b],),
            )


class ApplicationIsolationTests(unittest.TestCase, TwoOwnerFixture):
    def setUp(self):
        self.provision()
        self.provision_conversations()
        self.addCleanup(self.discard)

    def test_conversation_listings_never_include_another_owner(self):
        with connection(self.database_url, readonly=True) as database:
            listed = list_conversations(database, owner_id=self.owner_a)

        self.assertEqual(
            [str(row["id"]) for row in listed],
            [self.conversations[self.owner_a]],
        )

    def test_loading_another_owners_conversation_returns_nothing(self):
        with connection(self.database_url, readonly=True) as database:
            found = load_conversation(
                database,
                self.conversations[self.owner_b],
                owner_id=self.owner_a,
            )

        # Missing and someone else's are deliberately indistinguishable.
        self.assertIsNone(found)

    def test_another_owners_turns_are_not_readable(self):
        with connection(self.database_url, readonly=True) as database:
            turns = load_turns(
                database,
                self.conversations[self.owner_b],
                owner_id=self.owner_a,
            )

        self.assertEqual(turns, [])

    def test_deleting_another_owners_conversation_is_refused(self):
        with connection(self.database_url) as database:
            removed = delete_conversation(
                database,
                self.conversations[self.owner_b],
                owner_id=self.owner_a,
            )
            survivor = load_conversation(
                database,
                self.conversations[self.owner_b],
                owner_id=self.owner_b,
            )

        self.assertFalse(removed)
        self.assertIsNotNone(survivor)

    def test_book_listings_never_include_another_owner(self):
        with connection(self.database_url) as database:
            listed = list_books(database, owner_id=self.owner_a)

        self.assertEqual([row["id"] for row in listed], [self.books[self.owner_a]])

    def test_ready_book_lookup_refuses_another_owners_book(self):
        with connection(self.database_url) as database:
            self.assertIsNone(
                ready_book(database, self.books[self.owner_b], owner_id=self.owner_a)
            )
            self.assertIsNotNone(
                ready_book(database, self.books[self.owner_a], owner_id=self.owner_a)
            )

    def test_restoring_another_owners_book_fails(self):
        with connection(self.database_url) as database:
            with self.assertRaises(KeyError):
                restore_book(
                    database, self.books[self.owner_b], owner_id=self.owner_a
                )

    def test_identical_file_hashes_stay_in_separate_libraries(self):
        with connection(self.database_url) as database:
            rows = database.execute(
                "select owner_id, id from books where file_hash = %s order by id",
                ("b" * 64,),
            ).fetchall()

        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["owner_id"], rows[1]["owner_id"])

    def test_retrieval_only_returns_the_requesting_owners_chunks(self):
        config = ChunkingConfig()
        with connection(self.database_url) as database:
            for owner in (self.owner_a, self.owner_b):
                rebuild(database, self.books[owner], owner_id=owner, config=config)

            results = search(database, "chapter introduction", owner_id=self.owner_a)

        self.assertTrue(results)
        for result in results:
            self.assertEqual(result.source_book_id, self.books[self.owner_a])

    def test_marking_another_owners_book_ready_is_refused(self):
        with connection(self.database_url) as database:
            database.execute(
                "update books set status = 'processing', ready_at = null "
                "where id = %s",
                (self.books[self.owner_b],),
            )
            with self.assertRaises(KeyError):
                mark_book_ready(
                    database, self.books[self.owner_b], owner_id=self.owner_a
                )

            status = database.execute(
                "select status from books where id = %s",
                (self.books[self.owner_b],),
            ).fetchone()["status"]

        self.assertEqual(status, "processing")


class RowLevelSecurityTests(unittest.TestCase, TwoOwnerFixture):
    """The same questions asked as an authenticated database role.

    Application code connects with a privileged role, so these assertions
    cover the second line of defence: direct PostgREST-style access.
    """

    def setUp(self):
        self.provision()
        self.provision_conversations()
        self.addCleanup(self.discard)
        with connection(self.database_url) as database:
            self.job_b = database.execute(
                """
                insert into ingestion_jobs (
                    owner_id, idempotency_key, status, storage_bucket,
                    storage_path, storage_backend, original_filename
                ) values (
                    %s, %s, 'queued', 'book-sources', %s, 'supabase', 'b.pdf'
                )
                returning id
                """,
                (self.owner_b, uuid4(), f"{self.owner_b}/job/original.pdf"),
            ).fetchone()["id"]

    def _as_owner(self, database, owner: str) -> None:
        database.execute("set local role authenticated")
        database.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub": "{owner}", "role": "authenticated"}}',),
        )

    def test_authenticated_role_sees_only_its_own_rows(self):
        with connection(self.database_url) as database:
            with database.transaction():
                self._as_owner(database, self.owner_a)
                visible = database.execute("select id from books").fetchall()
                jobs = database.execute("select id from ingestion_jobs").fetchall()

        self.assertEqual([row["id"] for row in visible], [self.books[self.owner_a]])
        self.assertEqual(jobs, [])

    def test_authenticated_role_sees_only_its_own_conversations(self):
        with connection(self.database_url) as database:
            with database.transaction():
                self._as_owner(database, self.owner_a)
                conversations = database.execute(
                    "select id, title from conversations"
                ).fetchall()
                turns = database.execute(
                    "select question from conversation_turns"
                ).fetchall()

        self.assertEqual(
            [str(row["id"]) for row in conversations],
            [self.conversations[self.owner_a]],
        )
        self.assertEqual(
            [row["question"] for row in turns],
            [f"{self.owner_a} private question"],
        )

    def test_authenticated_role_cannot_delete_another_owners_conversation(self):
        with connection(self.database_url) as database:
            with database.transaction():
                self._as_owner(database, self.owner_a)
                database.execute(
                    "delete from conversations where id = %s",
                    (self.conversations[self.owner_b],),
                )
            with database.transaction():
                self._as_owner(database, self.owner_b)
                survivors = database.execute(
                    "select id from conversations"
                ).fetchall()

        # The delete matched no visible row rather than raising, which is how
        # RLS refuses: the other owner's conversation is simply still there.
        self.assertEqual(
            [str(row["id"]) for row in survivors],
            [self.conversations[self.owner_b]],
        )

    def test_authenticated_role_cannot_forge_a_conversation_for_another_owner(self):
        with connection(self.database_url) as database:
            with self.assertRaises(postgres_errors.InsufficientPrivilege):
                with database.transaction():
                    self._as_owner(database, self.owner_a)
                    database.execute(
                        """
                        insert into conversations (owner_id, title, book_ids)
                        values (%s, 'forged', %s)
                        """,
                        (self.owner_b, [self.books[self.owner_b]]),
                    )

    def test_authenticated_role_cannot_read_another_owners_content(self):
        with connection(self.database_url) as database:
            with database.transaction():
                self._as_owner(database, self.owner_a)
                for table in ("nodes", "content_blocks", "chunks"):
                    rows = database.execute(
                        f"select owner_id from {table}"
                    ).fetchall()
                    self.assertTrue(
                        all(
                            UUID(str(row["owner_id"])) == UUID(self.owner_a)
                            for row in rows
                        ),
                        f"{table} leaked another owner's rows",
                    )

    def test_authenticated_role_cannot_write_jobs_directly(self):
        # Job writes belong to the API and worker; the browser only reads.
        with connection(self.database_url) as database:
            with self.assertRaises(postgres_errors.InsufficientPrivilege):
                with database.transaction():
                    self._as_owner(database, self.owner_a)
                    database.execute(
                        """
                        insert into ingestion_jobs (
                            owner_id, idempotency_key, status, storage_bucket,
                            storage_path, storage_backend, original_filename
                        ) values (
                            %s, %s, 'queued', 'book-sources', %s, 'supabase',
                            'x.pdf'
                        )
                        """,
                        (self.owner_a, uuid4(), f"{self.owner_a}/x/original.pdf"),
                    )

    def test_authenticated_role_cannot_claim_another_owners_book(self):
        with connection(self.database_url) as database:
            with database.transaction():
                self._as_owner(database, self.owner_a)
                updated = database.execute(
                    "update books set title = 'stolen' where id = %s",
                    (self.books[self.owner_b],),
                ).rowcount

        self.assertEqual(updated, 0)


if __name__ == "__main__":
    unittest.main()
