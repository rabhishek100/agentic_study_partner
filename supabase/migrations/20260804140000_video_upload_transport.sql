-- First-class upload facts for the raw video transport. The API verifies the
-- transfer; the video worker remains responsible for probing and promoting a
-- playable source to canonical storage.

alter table video.ingestion_jobs
    add column declared_size_bytes bigint check (
        declared_size_bytes is null or declared_size_bytes > 0
    ),
    add column declared_media_type text,
    add column staging_size_bytes bigint check (
        staging_size_bytes is null or staging_size_bytes > 0
    ),
    add column staging_content_hash text check (
        staging_content_hash is null
        or staging_content_hash ~ '^[0-9a-f]{64}$'
    ),
    add column upload_completed_at timestamptz;

update video.ingestion_jobs
set declared_size_bytes = (provenance_json ->> 'declared_size_bytes')::bigint,
    declared_media_type = provenance_json ->> 'media_type'
where provenance_json ? 'declared_size_bytes';

alter table video.ingestion_jobs
    add constraint video_jobs_declared_upload_pair check (
        (declared_size_bytes is null) = (declared_media_type is null)
    ),
    add constraint video_jobs_staging_upload_triplet check (
        (staging_size_bytes is null
            and staging_content_hash is null
            and upload_completed_at is null)
        or
        (staging_size_bytes is not null
            and staging_content_hash is not null
            and upload_completed_at is not null)
    ),
    add constraint video_jobs_staging_within_declaration check (
        staging_size_bytes is null
        or declared_size_bytes is null
        or staging_size_bytes <= declared_size_bytes
    ),
    add constraint video_jobs_awaiting_upload_has_declaration check (
        status <> 'awaiting_upload'
        or (declared_size_bytes is not null and declared_media_type is not null)
    );

comment on column video.ingestion_jobs.staging_content_hash is
    'SHA-256 verified while streaming upload bytes; playable media validation happens in the worker.';
