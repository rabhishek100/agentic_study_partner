"""Derived data from ingestion versions nothing points at any more.

Re-ingesting a video writes a new version and leaves the previous one whole:
its evidence units, frames, visual observations, regions and embeddings all
stay. That is deliberate — a rebuild that fails must leave the published
version standing — but nothing ever removes the copies afterwards, so every
recovery run, quality upgrade and retry adds a full set that no answer can
reach. One video in the local corpus carries six versions.

The rows are derived and rebuildable, so they have no claim on the space.
Three things do, though, and each is a reason to keep a version:

- It is the video's current version. Obvious, and enforced twice: the query
  excludes it and the foreign key would refuse anyway.
- An answer was grounded in it. `conversation_turns` and
  `course_turn_versions` record the version a citation came from, and those
  references deliberately do not cascade. Deleting the version would either
  fail or strand a citation that can no longer be explained, and an answer
  whose provenance has been deleted is worse than no answer.
- A job is still working on it. An in-flight version looks superseded from
  the outside — the video still points at whatever was last published — and
  deleting it would destroy the work in progress.

What is left after those is genuinely unreachable, and one previous version
per video is still kept by default so a rollback has somewhere to go.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from uuid import UUID

from psycopg import Connection


logger = logging.getLogger("study_partner.video.version_retention")

DEFAULT_KEEP_PREVIOUS = 1
ACTIVE_JOB_STATUSES = ("awaiting_upload", "queued", "running", "retry_scheduled")

# Versions that may be removed, newest first within each video so the caller
# can keep the most recent few.
PRUNABLE = f"""
    select iv.id, iv.owner_id, iv.video_id, iv.version_number,
           row_number() over (
               partition by iv.video_id order by iv.version_number desc
           ) as recency
    from video.ingestion_versions as iv
    join video.videos as v
      on v.id = iv.video_id and v.owner_id = iv.owner_id
    where iv.id is distinct from v.current_ingestion_version_id
      and not exists (
          select 1 from video.conversation_turns as t
          where t.ingestion_version_id = iv.id
      )
      and not exists (
          select 1 from video.course_turn_versions as c
          where c.ingestion_version_id = iv.id
      )
      and not exists (
          select 1 from video.ingestion_jobs as j
          where j.target_version_id = iv.id
            and j.status in {ACTIVE_JOB_STATUSES}
      )
    order by iv.video_id, iv.version_number desc
"""


def keep_previous() -> int:
    """How many superseded versions per video survive, for rollback."""

    raw = os.getenv("VIDEO_VERSION_KEEP_PREVIOUS", "").strip()
    if not raw:
        return DEFAULT_KEEP_PREVIOUS
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError("VIDEO_VERSION_KEEP_PREVIOUS must be an integer") from error
    if value < 0:
        raise ValueError("VIDEO_VERSION_KEEP_PREVIOUS must not be negative")
    return value


def dry_run_requested() -> bool:
    """Reporting is the default here too, for the same reason as the sweeps."""

    return os.getenv("VIDEO_VERSION_PRUNE_DRY_RUN", "").strip().lower() not in {
        "0",
        "false",
        "no",
    }


@dataclass(frozen=True)
class VersionPruneSummary:
    versions_removed: int = 0
    evidence_units_removed: int = 0
    embeddings_removed: int = 0
    frames_removed: int = 0
    versions_kept_for_rollback: int = 0
    versions_kept_for_citations: int = 0
    dry_run: bool = False


def _counts(connection: Connection, ids: list[UUID]) -> tuple[int, int, int]:
    if not ids:
        return 0, 0, 0
    row = connection.execute(
        """
        select
          (select count(*) from video.evidence_units
            where ingestion_version_id = any(%(ids)s::uuid[])) as evidence,
          (select count(*) from video.evidence_embeddings
            where ingestion_version_id = any(%(ids)s::uuid[])) as embeddings,
          (select count(*) from video.frames
            where ingestion_version_id = any(%(ids)s::uuid[])) as frames
        """,
        {"ids": ids},
    ).fetchone()
    return int(row["evidence"]), int(row["embeddings"]), int(row["frames"])


def prune_superseded_versions(
    connection: Connection,
    *,
    keep: int | None = None,
    dry_run: bool | None = None,
) -> VersionPruneSummary:
    """Remove derived data no version anything points at still needs.

    Deleting the version row cascades its evidence, frames, checkpoints and
    embeddings; the two tables that record where an answer came from do not
    cascade, and versions they name are excluded before we get here.
    """

    keep = keep_previous() if keep is None else keep
    dry_run = dry_run_requested() if dry_run is None else dry_run

    rows = connection.execute(PRUNABLE).fetchall()
    removable = [row for row in rows if row["recency"] > keep]
    kept_rollback = len(rows) - len(removable)

    cited = connection.execute(
        """
        select count(*) as kept from video.ingestion_versions as iv
        join video.videos as v
          on v.id = iv.video_id and v.owner_id = iv.owner_id
        where iv.id is distinct from v.current_ingestion_version_id
          and (
            exists (select 1 from video.conversation_turns as t
                     where t.ingestion_version_id = iv.id)
            or exists (select 1 from video.course_turn_versions as c
                        where c.ingestion_version_id = iv.id)
          )
        """
    ).fetchone()["kept"]

    ids = [row["id"] for row in removable]
    evidence, embeddings, frames = _counts(connection, ids)

    if ids and not dry_run:
        connection.execute(
            "delete from video.ingestion_versions where id = any(%s::uuid[])", (ids,)
        )

    for row in removable:
        logger.log(
            logging.INFO if dry_run else logging.WARNING,
            "%s superseded ingestion version %s (video %s, version %s)",
            "would remove" if dry_run else "removed",
            row["id"],
            row["video_id"],
            row["version_number"],
        )

    return VersionPruneSummary(
        versions_removed=len(ids),
        evidence_units_removed=evidence,
        embeddings_removed=embeddings,
        frames_removed=frames,
        versions_kept_for_rollback=kept_rollback,
        versions_kept_for_citations=int(cited),
        dry_run=dry_run,
    )
