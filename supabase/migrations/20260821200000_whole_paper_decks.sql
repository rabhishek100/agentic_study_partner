-- A paper is one complete document scope. It reuses the canonical book-backed
-- citation path, but unlike a chapter it has no single root node: papers can
-- have several top-level sections. The exact paper:<book_id> key is therefore
-- the persisted discriminator for the nullable node locator.

alter table public.decks
    drop constraint if exists decks_source_matches_kind;

alter table public.decks
    add constraint decks_source_matches_kind check (
        (
            source_kind = 'book'
            and book_id is not null
            and video_id is null
            and (
                node_id is not null
                or (
                    node_id is null
                    and generation_mode = 'topic_generated'
                    and scope_key = 'paper:' || book_id::text
                )
            )
        )
        or (
            source_kind = 'video'
            and video_id is not null
            and book_id is null
            and node_id is null
        )
    );

alter table public.deck_jobs
    drop constraint if exists deck_jobs_source_matches_kind;

alter table public.deck_jobs
    add constraint deck_jobs_source_matches_kind check (
        (
            source_kind = 'book'
            and book_id is not null
            and video_id is null
            and (
                node_id is not null
                or (
                    node_id is null
                    and generation_mode = 'topic_generated'
                    and scope_key = 'paper:' || book_id::text
                )
            )
        )
        or (
            source_kind = 'video'
            and video_id is not null
            and book_id is null
            and node_id is null
        )
    );

-- Decks already cascade through books; jobs did not. NOT VALID protects a
-- database with historical orphan diagnostics while enforcing ownership and
-- deletion integrity for every new whole-paper job.
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'deck_jobs_book_owner_fkey'
          and conrelid = 'public.deck_jobs'::regclass
    ) then
        alter table public.deck_jobs
            add constraint deck_jobs_book_owner_fkey
            foreign key (book_id, owner_id)
            references public.books (id, owner_id)
            on delete cascade
            not valid;
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from public.deck_jobs as job
        left join public.books as book
          on book.id = job.book_id and book.owner_id = job.owner_id
        where job.book_id is not null and book.id is null
    ) then
        alter table public.deck_jobs
            validate constraint deck_jobs_book_owner_fkey;
    end if;
end $$;

comment on column public.decks.scope_key is
    'Stable studied unit: book:<id>:node:<id>, paper:<id>, or video:<uuid>.';
comment on column public.deck_jobs.scope_key is
    'Stable studied unit: book:<id>:node:<id>, paper:<id>, or video:<uuid>.';
