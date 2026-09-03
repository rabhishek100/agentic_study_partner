"""The shadow identity registry: first use, repeat use, races, and collisions.

On Supabase the `auth.users` row was GoTrue's and always existed by the time a
token reached the API. On Railway it does not, so `ensure_application_user` is
what stands between a brand-new account and a foreign-key failure on its very
first authenticated request. These tests are mostly about the cases where it
must *not* quietly succeed.
"""

from __future__ import annotations

import threading
import unittest
from uuid import uuid4

from storage.application_users import (
    IdentityConflict,
    VerifiedIdentity,
    ensure_application_user,
    registered_owner_ids,
    reset_ensured_cache,
)
from storage.database import connection, resolve_database_url


class ApplicationUserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = resolve_database_url()
        reset_ensured_cache()
        self.addCleanup(reset_ensured_cache)
        self.created: list = []
        self.addCleanup(self._discard)

    def _discard(self) -> None:
        if not self.created:
            return
        with connection(self.database_url) as database:
            database.execute(
                "delete from auth.users where id = any(%s)", (self.created,)
            )

    def identity(self, email: str | None = None) -> VerifiedIdentity:
        owner_id = uuid4()
        self.created.append(owner_id)
        return VerifiedIdentity(owner_id=owner_id, email=email)

    def row_for(self, owner_id):
        with connection(self.database_url) as database:
            return database.execute(
                "select id, email from auth.users where id = %s", (owner_id,)
            ).fetchone()

    def test_a_new_identity_is_registered_on_first_use(self):
        identity = self.identity("first@example.test")
        with connection(self.database_url) as database:
            wrote = ensure_application_user(database, identity)

        self.assertTrue(wrote)
        row = self.row_for(identity.owner_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["email"], "first@example.test")

    def test_a_repeat_request_does_not_write_again(self):
        identity = self.identity("repeat@example.test")
        with connection(self.database_url) as database:
            ensure_application_user(database, identity)
            again = ensure_application_user(database, identity)
        self.assertFalse(again)

    def test_a_repeat_request_is_a_no_op_even_without_the_cache(self):
        """The cache is an optimisation. Correctness must not depend on it."""

        identity = self.identity("nocache@example.test")
        with connection(self.database_url) as database:
            ensure_application_user(database, identity, use_cache=False)
            ensure_application_user(database, identity, use_cache=False)

        with connection(self.database_url) as database:
            count = database.execute(
                "select count(*) as n from auth.users where id = %s",
                (identity.owner_id,),
            ).fetchone()
        self.assertEqual(count["n"], 1)

    def test_a_changed_email_updates_the_same_identity(self):
        identity = self.identity("before@example.test")
        with connection(self.database_url) as database:
            ensure_application_user(database, identity)
        renamed = VerifiedIdentity(owner_id=identity.owner_id, email="after@example.test")
        with connection(self.database_url) as database:
            ensure_application_user(database, renamed)

        self.assertEqual(self.row_for(identity.owner_id)["email"], "after@example.test")

    def test_a_token_without_an_email_keeps_the_recorded_one(self):
        """A token that omits `email` must not blank the registry's copy."""

        identity = self.identity("kept@example.test")
        with connection(self.database_url) as database:
            ensure_application_user(database, identity)
        anonymous = VerifiedIdentity(owner_id=identity.owner_id, email=None)
        with connection(self.database_url) as database:
            ensure_application_user(database, anonymous, use_cache=False)

        self.assertEqual(self.row_for(identity.owner_id)["email"], "kept@example.test")

    def test_an_email_belonging_to_another_identity_is_refused(self):
        """The case that must never resolve itself.

        `email` is unique here, so silently reassigning it would hand one
        account's entire library to whoever presented a token claiming that
        address. It raises instead, and the caller answers 409.
        """

        established = self.identity("shared@example.test")
        with connection(self.database_url) as database:
            ensure_application_user(database, established)

        impostor = self.identity("shared@example.test")
        with connection(self.database_url) as database:
            with self.assertRaises(IdentityConflict):
                ensure_application_user(database, impostor)

        # The established identity is untouched and the new one was not created.
        self.assertEqual(self.row_for(established.owner_id)["email"], "shared@example.test")
        self.assertIsNone(self.row_for(impostor.owner_id))

    def test_two_simultaneous_first_requests_produce_one_row(self):
        """Two tabs, one new account, one row and no error in either request."""

        identity = self.identity("race@example.test")
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def register() -> None:
            try:
                barrier.wait(timeout=10)
                with connection(self.database_url) as database:
                    ensure_application_user(database, identity, use_cache=False)
            except BaseException as error:  # noqa: BLE001 - reported, not swallowed
                errors.append(error)

        threads = [threading.Thread(target=register) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(errors, [], f"concurrent registration raised: {errors}")
        with connection(self.database_url) as database:
            count = database.execute(
                "select count(*) as n from auth.users where id = %s",
                (identity.owner_id,),
            ).fetchone()
        self.assertEqual(count["n"], 1)

    def test_registered_owner_ids_reports_the_registry(self):
        identity = self.identity("listed@example.test")
        with connection(self.database_url) as database:
            ensure_application_user(database, identity)
            known = registered_owner_ids(database)
        self.assertIn(identity.owner_id, known)


if __name__ == "__main__":
    unittest.main()
