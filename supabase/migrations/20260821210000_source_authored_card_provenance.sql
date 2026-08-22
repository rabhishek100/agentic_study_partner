-- Source-authored cards are derived views of exact book material. Preserve
-- their identity and prompt/solution provenance separately so repeated labels
-- such as "Exercise 1" cannot hide a missing item.

alter table public.deck_cards
    add column if not exists source_item_key text,
    add column if not exists source_item_kind text,
    add column if not exists source_item_placement text,
    add column if not exists source_label text,
    add column if not exists source_discovery_method text,
    add column if not exists question_citations_json jsonb not null default '[]'::jsonb,
    add column if not exists answer_citations_json jsonb not null default '[]'::jsonb;

alter table public.deck_cards
    drop constraint if exists deck_cards_source_item_kind_check,
    add constraint deck_cards_source_item_kind_check check (
        source_item_kind is null
        or source_item_kind in ('exercise', 'worked_example')
    ),
    drop constraint if exists deck_cards_source_item_placement_check,
    add constraint deck_cards_source_item_placement_check check (
        source_item_placement is null
        or source_item_placement in ('inline', 'end_of_chapter')
    ),
    drop constraint if exists deck_cards_source_discovery_check,
    add constraint deck_cards_source_discovery_check check (
        source_discovery_method is null
        or source_discovery_method in (
            'numbered_section', 'explicit_label', 'model_fallback'
        )
    ),
    drop constraint if exists deck_cards_source_metadata_together,
    add constraint deck_cards_source_metadata_together check (
        (
            source_item_key is null
            and source_item_kind is null
            and source_item_placement is null
            and source_label is null
            and source_discovery_method is null
        )
        or (
            source_item_key is not null
            and source_item_kind is not null
            and source_item_placement is not null
            and source_label is not null
            and source_discovery_method is not null
        )
    ),
    drop constraint if exists deck_cards_question_citations_are_array,
    add constraint deck_cards_question_citations_are_array check (
        jsonb_typeof(question_citations_json) = 'array'
    ),
    drop constraint if exists deck_cards_answer_citations_are_array,
    add constraint deck_cards_answer_citations_are_array check (
        jsonb_typeof(answer_citations_json) = 'array'
    );

create unique index if not exists idx_deck_cards_source_item
    on public.deck_cards (deck_id, source_item_key)
    where source_item_key is not null;

alter table public.deck_jobs
    drop constraint if exists deck_jobs_automatic_key_matches_scope;
alter table public.deck_jobs
    add constraint deck_jobs_automatic_key_matches_scope check (
        automatic_key is null or automatic_key = 'auto:set1:' || scope_key
    );

comment on column public.deck_cards.source_item_key is
    'Stable detector identity for one source-authored exercise or worked example.';
comment on column public.deck_cards.question_citations_json is
    'Canonical citations supporting the source-authored prompt/setup.';
comment on column public.deck_cards.answer_citations_json is
    'Canonical citations supporting the printed or grounded solution.';
