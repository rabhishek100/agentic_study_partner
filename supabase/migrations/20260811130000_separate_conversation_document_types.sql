-- Separate conversation history between technical books and scientific papers
alter table public.conversations
    add column if not exists document_type text not null default 'book';

create index if not exists idx_conversations_owner_doc_type
    on public.conversations (owner_id, document_type, updated_at desc);

-- Backfill conversations whose book_ids contain scientific papers
update public.conversations
set document_type = 'paper'
where exists (
    select 1
    from unnest(conversations.book_ids) as b_id
    join public.books on books.id = b_id
    where books.document_type = 'paper'
);
