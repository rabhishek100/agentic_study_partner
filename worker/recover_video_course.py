"""Run the ordinary video worker with production R2 as an acquisition source."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
from uuid import UUID

import boto3
from dotenv import load_dotenv

from storage.database import close_pools
from video.media_store import configured_media_store
from video.pipeline import VideoPipelineDependencies
from video.recovery_acquisition import S3YouTubeRecoveryAcquirer
from video.states import Stage
from video.worker import VideoWorker
from worker.main import configure_logging


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _recovery_acquirer() -> S3YouTubeRecoveryAcquirer:
    client = boto3.client(
        "s3",
        endpoint_url=_required("VIDEO_RECOVERY_S3_ENDPOINT"),
        region_name=os.getenv("VIDEO_RECOVERY_S3_REGION", "auto").strip() or "auto",
        aws_access_key_id=_required("VIDEO_RECOVERY_S3_ACCESS_KEY_ID"),
        aws_secret_access_key=_required("VIDEO_RECOVERY_S3_SECRET_ACCESS_KEY"),
    )
    return S3YouTubeRecoveryAcquirer(
        client=client,
        bucket=_required("VIDEO_RECOVERY_S3_BUCKET"),
        owner_id=_required("VIDEO_RECOVERY_OWNER_ID"),
    )


def _evict_owner_cache(owner_id: UUID) -> None:
    configured = os.getenv("VIDEO_MEDIA_CACHE_ROOT", "").strip()
    if not configured:
        return
    root = Path(configured).expanduser().resolve()
    owner_cache = (root / str(owner_id)).resolve()
    if root not in owner_cache.parents or owner_cache.name != str(owner_id):
        raise RuntimeError("refusing to evict an unsafe media cache path")
    shutil.rmtree(owner_cache, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover video acquisition artifacts from another R2 bucket"
    )
    parser.add_argument("--worker-id", default="video-r2-recovery")
    parser.add_argument("--database-url")
    parser.add_argument(
        "--stages",
        help="Comma-separated stage allowlist; defaults to every video stage",
    )
    parser.add_argument(
        "--max-stages", type=int, default=1, help="Maximum checkpoints to process"
    )
    parser.add_argument(
        "--evict-cache-after-publish", action="store_true"
    )
    return parser


def main() -> None:
    load_dotenv()
    configure_logging()
    arguments = build_parser().parse_args()
    if arguments.max_stages <= 0:
        raise ValueError("--max-stages must be positive")
    supported = None
    if arguments.stages:
        supported = frozenset(
            Stage(value.strip())
            for value in arguments.stages.split(",")
            if value.strip()
        )
    dependencies = VideoPipelineDependencies(
        media_store=configured_media_store(),
        youtube_acquirer=_recovery_acquirer(),
        youtube_acquisition_version="r2-recovery-v1",
    )
    worker = VideoWorker(
        worker_id=arguments.worker_id,
        database_url=arguments.database_url,
        dependencies=dependencies,
        supported_stages=supported,
    )
    try:
        worker.recover_abandoned_jobs()
        for _ in range(arguments.max_stages):
            job = worker.claim()
            if job is None:
                break
            worker.process(job)
            if arguments.evict_cache_after_publish and job.stage is Stage.PUBLISH:
                _evict_owner_cache(job.owner_id)
    finally:
        close_pools()


if __name__ == "__main__":
    main()
