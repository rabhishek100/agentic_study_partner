-- Canonical records for standalone videos, their supporting resources, and
-- course-ready relationships. Ingestion runs and all rebuildable evidence
-- live in later migrations; these tables describe durable source material.

create schema if not exists video;

revoke all on schema video from public;
grant usage on schema video to authenticated;

create table video.videos (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    title text not null check (btrim(title) <> ''),
    description text,
    source_kind text not null check (source_kind in ('youtube', 'upload')),
    duration_ms bigint check (duration_ms is null or duration_ms > 0),
    readiness_status text not null default 'processing' check (
        readiness_status in ('processing', 'ready', 'degraded', 'failed')
    ),
    playback_json jsonb not null default '{}'::jsonb check (
        jsonb_typeof(playback_json) = 'object'
    ),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    ready_at timestamptz,
    unique (id, owner_id),
    unique (id, owner_id, source_kind),
    check (
        (readiness_status in ('ready', 'degraded')) = (ready_at is not null)
    )
);

create table video.video_sources (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null,
    video_id uuid not null,
    source_kind text not null check (source_kind in ('youtube', 'upload')),
    status text not null default 'pending' check (
        status in ('pending', 'acquiring', 'ready', 'failed')
    ),
    is_primary boolean not null default true,
    source_url text,
    youtube_video_id text,
    original_filename text,
    storage_backend text check (
        storage_backend is null
        or storage_backend in ('filesystem', 'supabase', 's3')
    ),
    storage_key text,
    content_hash text check (
        content_hash is null or content_hash ~ '^[0-9a-f]{64}$'
    ),
    size_bytes bigint check (size_bytes is null or size_bytes > 0),
    media_type text,
    media_metadata_json jsonb not null default '{}'::jsonb check (
        jsonb_typeof(media_metadata_json) = 'object'
    ),
    acquisition_provider text,
    acquisition_version text,
    provenance_json jsonb not null default '{}'::jsonb check (
        jsonb_typeof(provenance_json) = 'object'
    ),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    acquired_at timestamptz,
    unique (id, video_id, owner_id),
    foreign key (video_id, owner_id, source_kind)
        references video.videos(id, owner_id, source_kind) on delete cascade,
    check (
        (storage_backend is null and storage_key is null)
        or (
            storage_backend is not null
            and storage_key is not null
            and btrim(storage_key) <> ''
            and storage_key like owner_id::text || '/%'
        )
    ),
    check (
        (source_kind = 'youtube'
            and source_url is not null
            and btrim(source_url) <> ''
            and youtube_video_id is not null
            and btrim(youtube_video_id) <> ''
            and original_filename is null)
        or
        (source_kind = 'upload'
            and source_url is null
            and youtube_video_id is null
            and original_filename is not null
            and btrim(original_filename) <> '')
    ),
    check (
        status <> 'ready'
        or (
            storage_backend is not null
            and storage_key is not null
            and content_hash is not null
            and size_bytes is not null
            and media_type is not null
            and btrim(media_type) <> ''
            and acquired_at is not null
        )
    )
);

create unique index idx_video_sources_primary
    on video.video_sources (owner_id, video_id)
    where is_primary;
create unique index idx_video_sources_youtube_id
    on video.video_sources (owner_id, youtube_video_id)
    where youtube_video_id is not null;
create unique index idx_video_sources_content
    on video.video_sources (owner_id, video_id, content_hash)
    where content_hash is not null;
create index idx_video_sources_owner_video_created
    on video.video_sources (owner_id, video_id, created_at desc);

create table video.chapters (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null,
    video_id uuid not null,
    video_source_id uuid not null,
    chapter_index integer not null check (chapter_index >= 0),
    chapter_kind text not null check (chapter_kind in ('youtube', 'manual')),
    title text not null check (btrim(title) <> ''),
    start_ms bigint not null check (start_ms >= 0),
    end_ms bigint not null check (end_ms > start_ms),
    provenance_json jsonb not null default '{}'::jsonb check (
        jsonb_typeof(provenance_json) = 'object'
    ),
    created_at timestamptz not null default now(),
    unique (id, video_id, owner_id),
    unique (video_source_id, chapter_index),
    unique (video_source_id, start_ms),
    foreign key (video_id, owner_id)
        references video.videos(id, owner_id) on delete cascade,
    foreign key (video_source_id, video_id, owner_id)
        references video.video_sources(id, video_id, owner_id) on delete cascade
);

create index idx_video_chapters_owner_video_start
    on video.chapters (owner_id, video_id, start_ms);

create table video.resources (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    resource_kind text not null check (
        resource_kind in ('pdf', 'external_link')
    ),
    origin text not null check (origin in ('upload', 'url', 'discovered')),
    status text not null default 'pending' check (
        status in ('pending', 'processing', 'ready', 'failed')
    ),
    title text not null check (btrim(title) <> ''),
    source_url text,
    original_filename text,
    storage_backend text check (
        storage_backend is null
        or storage_backend in ('filesystem', 'supabase', 's3')
    ),
    storage_key text,
    content_hash text check (
        content_hash is null or content_hash ~ '^[0-9a-f]{64}$'
    ),
    size_bytes bigint check (size_bytes is null or size_bytes > 0),
    media_type text,
    page_count integer check (page_count is null or page_count > 0),
    provenance_json jsonb not null default '{}'::jsonb check (
        jsonb_typeof(provenance_json) = 'object'
    ),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (id, owner_id),
    check (
        (storage_backend is null and storage_key is null)
        or (
            storage_backend is not null
            and storage_key is not null
            and btrim(storage_key) <> ''
            and storage_key like owner_id::text || '/%'
        )
    ),
    check (
        (origin = 'upload'
            and original_filename is not null
            and btrim(original_filename) <> '')
        or
        (origin in ('url', 'discovered')
            and source_url is not null
            and btrim(source_url) <> '')
    ),
    check (
        (resource_kind = 'external_link'
            and status = 'ready'
            and source_url is not null
            and storage_backend is null
            and storage_key is null
            and content_hash is null
            and size_bytes is null
            and media_type is null
            and page_count is null)
        or
        (resource_kind = 'pdf'
            and (
                status <> 'ready'
                or (
                    storage_backend is not null
                    and storage_key is not null
                    and content_hash is not null
                    and size_bytes is not null
                    and media_type = 'application/pdf'
                    and page_count is not null
                )
            ))
    )
);

create unique index idx_video_resources_content
    on video.resources (owner_id, content_hash)
    where resource_kind = 'pdf' and content_hash is not null;
create index idx_video_resources_owner_updated
    on video.resources (owner_id, updated_at desc);

create table video.video_resources (
    owner_id uuid not null,
    video_id uuid not null,
    resource_id uuid not null,
    role text not null check (role in ('slides', 'notes', 'reference')),
    required boolean not null default false,
    attached_at timestamptz not null default now(),
    primary key (video_id, resource_id),
    foreign key (video_id, owner_id)
        references video.videos(id, owner_id) on delete cascade,
    foreign key (resource_id, owner_id)
        references video.resources(id, owner_id) on delete cascade
);

create index idx_video_video_resources_owner_video
    on video.video_resources (owner_id, video_id, attached_at);
create index idx_video_video_resources_owner_resource
    on video.video_resources (owner_id, resource_id);

create table video.courses (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    title text not null check (btrim(title) <> ''),
    description text,
    metadata_json jsonb not null default '{}'::jsonb check (
        jsonb_typeof(metadata_json) = 'object'
    ),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (id, owner_id)
);

create index idx_video_courses_owner_updated
    on video.courses (owner_id, updated_at desc);

create table video.course_lectures (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null,
    course_id uuid not null,
    video_id uuid not null,
    lecture_index integer not null check (lecture_index >= 0),
    title_override text check (
        title_override is null or btrim(title_override) <> ''
    ),
    created_at timestamptz not null default now(),
    unique (id, course_id, owner_id),
    unique (course_id, lecture_index),
    unique (course_id, video_id),
    foreign key (course_id, owner_id)
        references video.courses(id, owner_id) on delete cascade,
    foreign key (video_id, owner_id)
        references video.videos(id, owner_id) on delete cascade
);

create index idx_video_course_lectures_owner_course
    on video.course_lectures (owner_id, course_id, lecture_index);

create table video.course_resources (
    owner_id uuid not null,
    course_id uuid not null,
    resource_id uuid not null,
    role text not null check (role in ('slides', 'notes', 'reference')),
    attached_at timestamptz not null default now(),
    primary key (course_id, resource_id),
    foreign key (course_id, owner_id)
        references video.courses(id, owner_id) on delete cascade,
    foreign key (resource_id, owner_id)
        references video.resources(id, owner_id) on delete cascade
);

create index idx_video_course_resources_owner_course
    on video.course_resources (owner_id, course_id, attached_at);
create index idx_video_course_resources_owner_resource
    on video.course_resources (owner_id, resource_id);

create or replace function video.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = clock_timestamp();
    return new;
end;
$$;

create trigger videos_set_updated_at
    before update on video.videos
    for each row execute function video.set_updated_at();
create trigger video_sources_set_updated_at
    before update on video.video_sources
    for each row execute function video.set_updated_at();
create trigger resources_set_updated_at
    before update on video.resources
    for each row execute function video.set_updated_at();
create trigger courses_set_updated_at
    before update on video.courses
    for each row execute function video.set_updated_at();

do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'videos', 'video_sources', 'chapters', 'resources',
        'video_resources', 'courses', 'course_lectures', 'course_resources'
    ] loop
        execute format('alter table video.%I enable row level security', table_name);
        execute format(
            'create policy %I on video.%I for select to authenticated '
            'using (owner_id = (select auth.uid()))',
            table_name || '_owner_read',
            table_name
        );
        execute format(
            'grant select on video.%I to authenticated',
            table_name
        );
    end loop;
end $$;

comment on schema video is
    'Owner-scoped video sources, resources, derived evidence, and workflows.';
comment on column video.video_sources.storage_key is
    'Backend-relative immutable content-addressed object key; binaries are not stored in Postgres.';
comment on column video.resources.storage_key is
    'Backend-relative immutable content-addressed object key; binaries are not stored in Postgres.';
