"""The application's own record of who an owner is.

Tenant tables carry `owner_id` foreign keys into `auth.users`. On Supabase that
table was GoTrue's, so a row appeared the moment someone signed up and the
foreign keys resolved for free. On Railway the database has no Auth service in
front of it, and the identity provider is a *different* system that this one
learns about only from a verified token. Something has to put the row there.

That is what this is: a shadow identity registry, written server-side after a
token has been verified, so a brand-new account's first authenticated request
does not fail on a foreign key. It is deliberately not a user profile store —
it holds the UUID the tokens carry and the email they claim, and nothing that
would make it a second source of truth about a person.

Preserving the UUID is the whole point. It is embedded in foreign keys across
every tenant table and in the R2 object keys, so an identity provider move
that minted new UUIDs would mean rewriting the corpus. The email may change;
the UUID may not.

What this refuses to do matters as much as what it does. It will not attach an
email to a UUID that is not already its own, because on this schema `email` is
unique and quietly reassigning it would silently transfer a library between
accounts. That case raises instead.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from uuid import UUID

import psycopg
from psycopg import Connection


logger = logging.getLogger(__name__)


class IdentityConflict(RuntimeError):
    """A verified identity cannot be reconciled with the registry.

    Raised rather than resolved: every way of resolving it automatically
    involves guessing which of two accounts owns a corpus.
    """


@dataclass(frozen=True)
class VerifiedIdentity:
    """The parts of a verified token this module is willing to persist."""

    owner_id: UUID
    email: str | None


class _EnsuredCache:
    """Remember which identities this process has already written.

    `ensure_application_user` is on the path of every authenticated request, so
    without this it would be an extra write per request forever. The cache is
    per-process and holds only UUIDs; losing it costs one redundant idempotent
    upsert, which is why it can be this simple.

    Bounded because it is keyed by something a caller influences — with an open
    signup, an unbounded set is a slow memory leak.
    """

    def __init__(self, maximum: int = 4096) -> None:
        self._lock = threading.Lock()
        self._seen: set[tuple[UUID, str | None]] = set()
        self._maximum = maximum

    def contains(self, key: tuple[UUID, str | None]) -> bool:
        with self._lock:
            return key in self._seen

    def add(self, key: tuple[UUID, str | None]) -> None:
        with self._lock:
            if len(self._seen) >= self._maximum:
                self._seen.clear()
            self._seen.add(key)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


_ENSURED = _EnsuredCache()


def reset_ensured_cache() -> None:
    """Forget which identities have been written. For tests and after a restore."""

    _ENSURED.clear()


# One statement, so two concurrent first requests cannot interleave a check and
# an insert. The `where` clause makes the update a no-op when nothing changed,
# which keeps a steady stream of requests from writing a row every time.
_UPSERT = """
insert into auth.users (id, email)
values (%(owner_id)s, %(email)s)
on conflict (id) do update
    set email = coalesce(excluded.email, auth.users.email)
    where auth.users.email is distinct from
          coalesce(excluded.email, auth.users.email)
"""


def ensure_application_user(
    connection: Connection,
    identity: VerifiedIdentity,
    *,
    use_cache: bool = True,
) -> bool:
    """Make the registry know this identity. Returns whether it wrote.

    Only ever called with a subject whose signature, issuer, audience, expiry
    and role have already been checked — this trusts its argument completely,
    which is safe exactly because `api.auth` will not construct one otherwise.

    Idempotent, and safe to race: the upsert is a single statement, so two
    simultaneous first requests produce one row and no error.
    """

    key = (identity.owner_id, identity.email)
    if use_cache and _ENSURED.contains(key):
        return False

    try:
        connection.execute(
            _UPSERT, {"owner_id": identity.owner_id, "email": identity.email}
        )
    except psycopg.errors.UniqueViolation as error:
        # A unique violation here is ambiguous, and the two cases could not
        # differ more. `on conflict (id)` names the primary key as its
        # arbiter, so a *concurrent insert of this same identity* can trip the
        # `email` index instead and raise rather than taking the update path —
        # entirely benign, two tabs opened at once. The other case is a real
        # collision: someone else's UUID already holds this address.
        #
        # The statement cannot tell them apart, so this looks.
        connection.rollback()
        if _email_belongs_elsewhere(connection, identity):
            logger.warning(
                "identity conflict for owner %s: the claimed email belongs to "
                "another identity",
                identity.owner_id,
            )
            raise IdentityConflict(
                "the email on this token belongs to a different account"
            ) from error
        if not _is_registered(connection, identity.owner_id):
            # Neither a benign race nor an email collision. Refusing is the
            # only safe answer: proceeding would let a request insert
            # tenant rows against an owner the registry does not know.
            raise IdentityConflict(
                "the identity could not be registered"
            ) from error
        # A concurrent request registered it. Nothing left to do.
        _ENSURED.add(key)
        return False

    _ENSURED.add(key)
    return True


def _email_belongs_elsewhere(connection: Connection, identity: VerifiedIdentity) -> bool:
    """Whether this token's email is already held by a different UUID."""

    if identity.email is None:
        return False
    row = connection.execute(
        "select id from auth.users where email = %s", (identity.email,)
    ).fetchone()
    if row is None:
        return False
    existing = row["id"] if isinstance(row, dict) else row[0]
    return existing != identity.owner_id


def _is_registered(connection: Connection, owner_id: UUID) -> bool:
    row = connection.execute(
        "select 1 from auth.users where id = %s", (owner_id,)
    ).fetchone()
    return row is not None


def registered_owner_ids(connection: Connection) -> set[UUID]:
    """Every identity the registry knows. Used by migration verification."""

    rows = connection.execute("select id from auth.users").fetchall()
    return {row["id"] if isinstance(row, dict) else row[0] for row in rows}
