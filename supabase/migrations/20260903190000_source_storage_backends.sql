-- Rows that name a source PDF now also name which provider holds it.
--
-- The bucket and path columns were provider-neutral by luck rather than by
-- design: "book-sources" and "{owner}/{job}/original.pdf" mean something in
-- Supabase Storage and something in R2, and nothing in the row said which.
-- While the migration runs both are true at once, so the row has to say.
--
-- That is what makes the move reversible one object at a time. The copier
-- verifies an object in R2 before flipping the rows that name it, so an
-- interrupted run leaves a database where some books read from Supabase and
-- some from R2, every one of them correct, and a rerun retries only what is
-- left. Rolling a single book back is an update, not a restore.
--
-- Existing rows are backfilled to 'supabase' rather than defaulted, because a
-- default would silently claim the same thing about rows written later, when
-- the answer is whatever the runtime is configured with.

alter table public.ingestion_jobs
    add column if not exists storage_backend text;

alter table public.books
    add column if not exists source_storage_backend text,
    add column if not exists viewer_storage_backend text;

-- Almost every object that exists today is in Supabase Storage, and the
-- exceptions say so in the column that used to have to carry the meaning on
-- its own: `bucket = 'local'` is how a book ingested from disk records that
-- there is no remote object at all. Two books are in that state. Backfilling
-- them as 'supabase' would point the migration at objects Storage has never
-- held, and it would report them as missing rather than as never-there.
update public.ingestion_jobs
set storage_backend = case when storage_bucket = 'local' then 'filesystem'
                           else 'supabase' end
where storage_backend is null;

update public.books
set source_storage_backend = case when source_storage_bucket = 'local'
                                  then 'filesystem' else 'supabase' end
where source_storage_backend is null and source_storage_bucket is not null;

update public.books
set viewer_storage_backend = case when viewer_storage_bucket = 'local'
                                  then 'filesystem' else 'supabase' end
where viewer_storage_backend is null and viewer_storage_bucket is not null;

-- `ingestion_jobs.storage_bucket` and `storage_path` are already NOT NULL, so
-- the backend is too: a job always names an object.
alter table public.ingestion_jobs
    alter column storage_backend set not null;

alter table public.ingestion_jobs
    drop constraint if exists ingestion_jobs_storage_backend_known;
alter table public.ingestion_jobs
    add constraint ingestion_jobs_storage_backend_known
    check (storage_backend in ('supabase', 'r2', 'filesystem'));

-- A book's source and viewer copies are each optional, but a half-described
-- object is not a state that should be representable: a row naming a bucket
-- and path with no backend is one nobody can open, and a row naming a backend
-- with no path is one that claims a copy it has not got.
alter table public.books
    drop constraint if exists books_source_storage_complete;
alter table public.books
    add constraint books_source_storage_complete check (
        (source_storage_backend is null
         and source_storage_bucket is null
         and source_storage_path is null)
        or (source_storage_backend in ('supabase', 'r2', 'filesystem')
            and source_storage_bucket is not null
            and source_storage_path is not null)
    );

alter table public.books
    drop constraint if exists books_viewer_storage_complete;
alter table public.books
    add constraint books_viewer_storage_complete check (
        (viewer_storage_backend is null
         and viewer_storage_bucket is null
         and viewer_storage_path is null)
        or (viewer_storage_backend in ('supabase', 'r2', 'filesystem')
            and viewer_storage_bucket is not null
            and viewer_storage_path is not null)
    );

-- The migration command and the cleanup sweep both ask "what is left on the
-- old provider", once per pass, over every row.
create index if not exists idx_books_source_storage_backend
    on public.books (source_storage_backend)
    where source_storage_backend is not null;

create index if not exists idx_ingestion_jobs_storage_backend
    on public.ingestion_jobs (storage_backend);

comment on column public.books.source_storage_backend is
    'Which provider holds this book''s source PDF. Set per row so the move to R2 is reversible one object at a time.';
comment on column public.ingestion_jobs.storage_backend is
    'Which provider holds this job''s uploaded PDF.';
