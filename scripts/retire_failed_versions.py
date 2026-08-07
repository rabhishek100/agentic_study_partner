"""Retire ingestion versions that failed and left nothing behind.

A failed attempt keeps its version row so the failure is explainable, which is
right while the failure is recent and worth reading. Long after, the row is
noise with a cost: the carried stages mean a version that published nothing
still holds a full copy of the frame, observation, and region rows it
inherited, and those are indistinguishable in the schema from the live ones.

Dry run by default. Nothing is deleted without `--apply`, because this removes
history and the point of keeping the row in the first place was that a missing
one must be explainable.

    uv run python -m scripts.retire_failed_versions --database-url "$MIGRATION_DATABASE_URL"
    uv run python -m scripts.retire_failed_versions --database-url "$MIGRATION_DATABASE_URL" --apply

A version is only eligible when every one of these holds:

- it is `failed` or `cancelled`, and was never published;
- it is not the video's current version;
- it produced no evidence units, so nothing was ever answered from it;
- no conversation turn cites it, so no transcript of a past answer is
  invalidated by removing it;
- no job for it is still running;
- it settled longer ago than the retention window.

Media is not touched here. Frame rows cascade, and the objects they named are
content-addressed and shared with the versions that carried the same frames
forward — so they stay referenced. Anything that does fall unreferenced is
`video.cleanup`'s to reclaim, which is the one order that cannot delete bytes
a surviving row still points at.
"""

from __future__ import annotations

import argparse
import os
import sys
from uuid import UUID

from psycopg.rows import dict_row
import psycopg


DEFAULT_RETENTION_DAYS = 7

ELIGIBLE = """
    select v.id, v.video_id, v.owner_id, v.version_number, v.status,
           v.completed_at, v.error_code,
           (select count(*) from video.frames f
             where f.ingestion_version_id = v.id) as frames,
           (select count(*) from video.visual_observations o
             where o.ingestion_version_id = v.id) as observations,
           (select count(*) from video.visual_regions r
             where r.ingestion_version_id = v.id) as regions,
           (select count(*) from video.ingestion_stage_checkpoints s
             where s.ingestion_version_id = v.id) as checkpoints
    from video.ingestion_versions as v
    join video.videos as video on video.id = v.video_id
    where v.status in ('failed', 'cancelled')
      and v.published_at is null
      and (video.current_ingestion_version_id is null
           or video.current_ingestion_version_id <> v.id)
      and v.completed_at is not null
      and v.completed_at < now() - make_interval(days => %(days)s)
      and not exists (
          select 1 from video.evidence_units e
          where e.ingestion_version_id = v.id
      )
      and not exists (
          select 1 from video.conversation_turns t
          where t.ingestion_version_id = v.id
      )
      and not exists (
          select 1 from video.ingestion_jobs j
          where j.target_version_id = v.id and j.status = 'running'
      )
    order by v.video_id, v.version_number
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL"),
    )
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--video-id", type=UUID, help="limit to one video")
    parser.add_argument(
        "--apply", action="store_true", help="actually delete the rows"
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        print("set --database-url, MIGRATION_DATABASE_URL, or DATABASE_URL")
        return 2
    if arguments.retention_days < 0:
        print("--retention-days cannot be negative")
        return 2

    with psycopg.connect(
        arguments.database_url, connect_timeout=20, row_factory=dict_row
    ) as connection:
        rows = connection.execute(
            ELIGIBLE, {"days": arguments.retention_days}
        ).fetchall()
        if arguments.video_id:
            rows = [row for row in rows if row["video_id"] == arguments.video_id]

        if not rows:
            print("no failed versions are eligible to retire")
            return 0

        print(f"{len(rows)} version(s) eligible:")
        for row in rows:
            print(
                f"  v{row['version_number']:<3} {row['status']:<10}"
                f" {row['error_code'] or '-':<20}"
                f" settled {row['completed_at']:%Y-%m-%d}"
                f"  frames={row['frames']} observations={row['observations']}"
                f" regions={row['regions']} checkpoints={row['checkpoints']}"
            )
        print(
            "\nEvidence units: 0 for every one of these, by the query that "
            "selected them.\nDeleting cascades their frames, observations, "
            "regions, checkpoints and job rows."
        )

        if not arguments.apply:
            print("\ndry run — pass --apply to delete")
            return 0

        deleted = 0
        for row in rows:
            with connection.transaction():
                # Re-checked under the row lock: the eligibility read above is
                # not held across the loop, and a retry could have claimed one
                # of these between the two.
                confirmed = connection.execute(
                    ELIGIBLE + " for update of v", {"days": arguments.retention_days}
                ).fetchall()
                if row["id"] not in {item["id"] for item in confirmed}:
                    print(f"  v{row['version_number']} is no longer eligible; skipped")
                    continue
                connection.execute(
                    "delete from video.ingestion_versions where id = %s and owner_id = %s",
                    (row["id"], row["owner_id"]),
                )
                deleted += 1
                print(f"  retired v{row['version_number']}")
        print(f"\nretired {deleted} version(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
