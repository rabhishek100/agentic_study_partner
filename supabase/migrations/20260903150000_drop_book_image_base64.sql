-- The figures are in object storage; drop the copy that was 52% of the database.
--
-- Every one of the 4,526 rows was verified against the bucket before this ran:
-- the object exists, its size matches the row, and both hashes are recorded.
-- `base64_hash` is what keeps figure captions keyed the way they already are,
-- so removing the text it was derived from changes nothing about captioning.
--
-- This is the step that actually reclaims the space. It is also the one that
-- makes object storage the only copy, which is why it was deliberately left
-- until the migration had been backfilled, verified and looked at.

alter table public.image_blocks
    drop constraint if exists image_blocks_has_content;

alter table public.image_blocks
    drop column if exists base64_content;

-- A figure is now defined by the object it names, so the key is required
-- rather than merely permitted.
alter table public.image_blocks
    alter column storage_key set not null,
    alter column storage_backend set not null;

alter table public.image_blocks
    drop constraint if exists image_blocks_storage_complete;
alter table public.image_blocks
    add constraint image_blocks_storage_complete check (
        content_hash ~ '^[0-9a-f]{64}$' and size_bytes > 0
    );

comment on column public.image_blocks.storage_key is
    'Owner-scoped, content-addressed object key. The figure lives here and nowhere else.';
comment on column public.image_blocks.base64_hash is
    'sha256 of the figure''s base64 text, retained because image_captions is keyed on it.';
