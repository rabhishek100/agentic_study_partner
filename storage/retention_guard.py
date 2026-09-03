"""Whether a database may be trusted to condemn objects in a bucket.

Both retention sweeps — book sources and video media — work the same way: list
what storage holds, ask the database which of it is still referenced, and
delete the rest. That argument is sound exactly as long as the database being
asked is the database those objects were written for.

It is not always. Restore an older dump, point a worker at a half-migrated
copy, run a test suite against an empty schema while the storage service still
serves the real bucket, and every surviving object looks unreferenced. The
sweep then stops reclaiming stranded bytes and starts deleting the library.

That is not hypothetical. It happened twice in two days:

- 2026-09-01, video media: a sweep against a database whose rows had been
  rolled back deleted 995 frame images out of R2.
- 2026-09-02, book sources: the worker's sweep ran during a test suite pointed
  at an empty database and deleted 52 of 54 book and paper PDFs. The two
  survivors were book-scoped viewer copies, which the sweep's path shape
  happens not to reach.

The rule lives here, once, because a safety rule kept in two places becomes
two rules.
"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger("study_partner.storage.retention_guard")

DEFAULT_MAX_ORPHAN_FRACTION = 0.25
DEFAULT_ORPHAN_GUARD_MINIMUM = 20


def max_orphan_fraction() -> float:
    """The orphan share past which the sweep suspects the database, not the bucket."""

    raw = os.getenv("STORAGE_CLEANUP_MAX_ORPHAN_FRACTION", "").strip()
    if not raw:
        return DEFAULT_MAX_ORPHAN_FRACTION
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(
            "STORAGE_CLEANUP_MAX_ORPHAN_FRACTION must be a number"
        ) from error
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            "STORAGE_CLEANUP_MAX_ORPHAN_FRACTION must be between 0 and 1"
        )
    return value


def orphan_guard_minimum() -> int:
    """How many eligible objects are needed before the share means anything.

    Below a handful, one stranded object beside one referenced one is 50% and
    a healthy owner mid-upload looks identical to a catastrophe. The guard
    exists to stop a stale database deleting a library, and a library is not
    four files, so under this floor the sweep proceeds and its per-pass budget
    remains the bound.
    """

    raw = os.getenv("STORAGE_CLEANUP_ORPHAN_GUARD_MINIMUM", "").strip()
    if not raw:
        return DEFAULT_ORPHAN_GUARD_MINIMUM
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(
            "STORAGE_CLEANUP_ORPHAN_GUARD_MINIMUM must be an integer"
        ) from error
    if value < 0:
        raise ValueError("STORAGE_CLEANUP_ORPHAN_GUARD_MINIMUM must not be negative")
    return value


def database_is_authoritative(
    *, scope: object, referenced: int, candidates: int, orphans: int
) -> bool:
    """Whether this database may be acted on for this scope.

    Two cheap questions separate a real orphan set from a database that has
    lost its rows. A scope with objects in the bucket and no referenced rows
    whatsoever is not one whose owner deleted everything; it is a database
    that has never heard of them. And a healthy bucket strands a few objects,
    not most of them, so an orphan share past the ceiling means the rows are
    missing rather than the objects stale.

    `scope` is whatever the caller names its unit of comparison — an owner id,
    a bucket — and appears in the refusal so the log says what was spared.
    """

    if candidates < orphan_guard_minimum():
        return True
    if referenced == 0:
        logger.error(
            "orphan sweep refused for %s: the database references none of the "
            "%s eligible objects storage holds. A database that does not know "
            "this scope cannot be treated as authoritative.",
            scope,
            candidates,
        )
        return False
    share = orphans / candidates
    ceiling = max_orphan_fraction()
    if share > ceiling:
        logger.error(
            "orphan sweep refused for %s: %s of %s eligible objects (%.0f%%) are "
            "unreferenced, past the %.0f%% ceiling. A database missing this many "
            "rows is likelier to be stale than the objects are.",
            scope,
            orphans,
            candidates,
            share * 100,
            ceiling * 100,
        )
        return False
    return True
