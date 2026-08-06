"""Give an already-published lecture the outline its source never shipped.

Chapters are derived data, so the pipeline builds them during indexing and a
re-ingest would produce them for free. This exists because a re-ingest of a
lecture that is already correct is a lot of machinery — and, for a hosted
model, real money — to run for a segmentation that needs no model call at all
and can be computed from frames that already exist.

It writes only `derived` chapters and refuses to touch a list the source
published or a person entered. Run it as often as you like: it replaces its own
output wholesale.
"""

import argparse
import json

from dotenv import load_dotenv

from storage.database import connection, environment_owner_id, parse_owner_id
from video.chapters import derive_chapters
from video.repository import replace_derived_chapters


def _arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-id", help="Owner UUID; defaults to DEFAULT_OWNER_ID")
    parser.add_argument("--video-id", help="One video; defaults to all ready ones")
    parser.add_argument("--database-url")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the outline that would be written and change nothing.",
    )
    return parser.parse_args()


def _timestamp(milliseconds: int) -> str:
    total = int(milliseconds) // 1000
    return f"{total // 60}:{total % 60:02d}"


def main():
    load_dotenv()
    args = _arguments()
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )

    with connection(args.database_url) as database:
        videos = database.execute(
            """
            select v.id, v.title, v.duration_ms,
                   v.current_ingestion_version_id as version_id
            from video.videos as v
            where v.owner_id = %s
              and v.readiness_status in ('ready', 'degraded')
              and v.current_ingestion_version_id is not null
              and (%s::uuid is null or v.id = %s::uuid)
            order by v.created_at
            """,
            (owner_id, args.video_id, args.video_id),
        ).fetchall()

        if not videos:
            raise SystemExit("No published videos matched.")

        for video in videos:
            observations = database.execute(
                """
                select frame.timestamp_ms, observation.visible_text
                from video.visual_observations as observation
                join video.frames as frame
                  on frame.id = observation.frame_id
                 and frame.owner_id = observation.owner_id
                where observation.owner_id = %s and observation.video_id = %s
                  and observation.ingestion_version_id = %s
                  and observation.status = 'success'
                order by frame.timestamp_ms
                """,
                (owner_id, video["id"], video["version_id"]),
            ).fetchall()
            chapters = derive_chapters(
                observations, duration_ms=int(video["duration_ms"] or 0)
            )

            print(f"\n{video['title']} — {len(observations)} analysed frames")
            if not chapters:
                print("  nothing readable on screen; leaving it without chapters")
                continue
            for chapter in chapters:
                print(
                    f"  {_timestamp(chapter.start_ms):>7} "
                    f"{_timestamp(chapter.end_ms):>7}  {chapter.title}"
                )

            if args.dry_run:
                print("  (dry run; nothing written)")
                continue

            written = replace_derived_chapters(
                database,
                owner_id=owner_id,
                video_id=video["id"],
                chapters=chapters,
            )
            if written < 0:
                print("  the source published its own chapters; left alone")
            else:
                print(f"  wrote {written} derived chapters")

    print("\n" + json.dumps({"videos": len(videos), "dry_run": args.dry_run}))


if __name__ == "__main__":
    main()
