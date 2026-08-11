-- Book-extracted cards and generation_mode support.

alter table public.decks
    add column generation_mode text not null default 'topic_generated'
        check (generation_mode in ('topic_generated', 'book_extracted'));

alter table public.deck_jobs
    add column generation_mode text not null default 'topic_generated'
        check (generation_mode in ('topic_generated', 'book_extracted'));

alter table public.deck_cards
    add column answer_source text
        check (answer_source is null or answer_source in ('printed_in_book', 'rag_generated'));

comment on column public.decks.generation_mode is
    'Source strategy: topic_generated (AI inventory) or book_extracted (questions from PDF text).';
comment on column public.deck_jobs.generation_mode is
    'Source strategy: topic_generated (AI inventory) or book_extracted (questions from PDF text).';
comment on column public.deck_cards.answer_source is
    'Provenance of the answer: printed_in_book or rag_generated. Null for video cards.';
