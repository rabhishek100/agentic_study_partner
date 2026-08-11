-- Support for scientific papers as a distinct document_type alongside books.
alter table public.books
    add column if not exists document_type text not null default 'book'
        check (document_type in ('book', 'paper'));

alter table public.ingestion_jobs
    add column if not exists document_type text not null default 'book'
        check (document_type in ('book', 'paper'));

create index if not exists idx_books_owner_document_type
    on public.books (owner_id, document_type, id desc);

create index if not exists idx_ingestion_jobs_owner_document_type
    on public.ingestion_jobs (owner_id, document_type, id desc);
