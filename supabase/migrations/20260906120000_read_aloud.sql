-- Read aloud: spoken figure descriptions, and a bounded cache of synthesised
-- audio.
--
-- A figure's ingest caption was written for retrieval: dense, noun-heavy, and
-- deliberately not prefaced ("Scatter of horsepower against weight, with the
-- fitted line…"). Heard rather than read, that lands as a fragment. A spoken
-- description is a second derived reading of the same image, written to be
-- listened to.
--
-- It is keyed on the image's own identity rather than on the block that
-- happens to carry it, for the reason captioning already discovered: the same
-- diagram appears in more than one book, and a re-parse that leaves the bytes
-- alone should not pay to describe them again. `image_blocks.base64_hash` is
-- that identity, and it is indexed per owner. Keying here rather than adding
-- columns to `image_captions` also means an uncaptioned figure — the caption
-- pass failed, or has not run — can still be described when a reader asks to
-- hear the answer that cites it.
--
-- Derived, and rebuildable: deleting a row re-describes on next use.
create table public.narration_figures (
    owner_id uuid not null references auth.users(id) on delete cascade,
    content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    description text not null check (length(description) between 1 and 2000),
    model_name text not null,
    generated_at timestamptz not null default now(),
    primary key (owner_id, content_hash)
);

alter table public.narration_figures enable row level security;

create policy narration_figures_owner_access on public.narration_figures
    for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

grant select, insert, update, delete on public.narration_figures to authenticated;

-- Synthesised speech, cached by exactly what produced it.
--
-- The key is a hash of the spoken text together with the model and voice, so
-- changing either re-synthesises rather than silently replaying the old voice.
-- Replaying an answer, or replaying the sentence a reader scrubbed back to, is
-- then free, which matters because per-character TTS pricing makes a re-listen
-- otherwise cost exactly as much as the first listen.
--
-- `last_used_at` exists so the cache can be evicted least-recently-used: this
-- table holds audio blobs on a small database, and a cache that only grows is
-- a disk-full incident waiting to happen. This project has had one.
create table public.narration_audio (
    owner_id uuid not null references auth.users(id) on delete cascade,
    content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    model_name text not null,
    voice text not null,
    media_type text not null check (media_type like 'audio/%'),
    audio bytea not null check (octet_length(audio) between 1 and 8000000),
    character_count integer not null check (character_count > 0),
    cost_usd numeric(10, 6) not null default 0 check (cost_usd >= 0),
    created_at timestamptz not null default now(),
    last_used_at timestamptz not null default now(),
    primary key (owner_id, content_hash)
);

-- The eviction scan: least recently heard first, within one owner.
create index idx_narration_audio_owner_last_used
    on public.narration_audio (owner_id, last_used_at);

alter table public.narration_audio enable row level security;

create policy narration_audio_owner_access on public.narration_audio
    for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

grant select, insert, update, delete on public.narration_audio to authenticated;
