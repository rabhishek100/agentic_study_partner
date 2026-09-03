-- Book figures move out of the database and into object storage.
--
-- `image_blocks` is 52% of this database: 4,526 JPEGs held as base64 text,
-- 456 MB of it, in a column whose TOAST is larger than every other table
-- combined. Base64 costs a further third over the bytes it encodes. None of
-- it needs to be in Postgres — a figure is addressable by key exactly like
-- video media already is, and the row stays canonical by naming it.
--
-- The column is kept and left nullable for now rather than dropped, so a
-- backfill can run, be verified, and be reverted without the figures having
-- ever existed in only one place. Dropping it is a later, separate change,
-- and the only one that actually reclaims the space.

alter table public.image_blocks
    add column if not exists storage_backend text,
    add column if not exists storage_key text,
    add column if not exists content_hash text,
    add column if not exists size_bytes bigint,
    -- The sha256 of the *base64 text*, not of the bytes. Figure captions are
    -- keyed on it and dedup boilerplate by it, and those keys are already
    -- persisted in image_captions. Recording it here keeps that identity
    -- stable once base64_content is gone; deriving a new identity from the
    -- raw bytes would invalidate every existing caption and re-buy 4,526
    -- vision calls to learn nothing.
    add column if not exists base64_hash text;

alter table public.image_blocks
    alter column base64_content drop not null;

-- A figure must be readable somewhere. Without this, a backfill that cleared
-- the column before writing the object would lose the image silently and the
-- row would still look well formed.
alter table public.image_blocks
    drop constraint if exists image_blocks_has_content;
alter table public.image_blocks
    add constraint image_blocks_has_content check (
        base64_content is not null or storage_key is not null
    );

alter table public.image_blocks
    drop constraint if exists image_blocks_storage_complete;
alter table public.image_blocks
    add constraint image_blocks_storage_complete check (
        storage_key is null
        or (
            storage_backend is not null
            and content_hash ~ '^[0-9a-f]{64}$'
            and size_bytes > 0
        )
    );

create index if not exists idx_image_blocks_owner_base64_hash
    on public.image_blocks (owner_id, base64_hash)
    where base64_hash is not null;

create index if not exists idx_image_blocks_owner_storage
    on public.image_blocks (owner_id, storage_key)
    where storage_key is not null;

comment on column public.image_blocks.base64_content is
    'Legacy inline figure bytes. Being migrated to object storage; dropped once every row carries a storage_key.';
comment on column public.image_blocks.storage_key is
    'Owner-scoped, content-addressed object key for this figure.';
