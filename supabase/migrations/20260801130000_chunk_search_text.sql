-- Index what a reader would search for, display what the page actually says.
--
-- Transcribed books carry mathematics as LaTeX, because a citation has to show
-- the notation the page prints. The full-text index does not want it: fed
-- `$\frac{\partial L}{\partial w}$`, the English text-search configuration
-- emits `frac`, `partial` and `w` as terms. Those dilute scoring on precisely
-- the chapters that are most mathematical, and no reader ever searches for
-- them.
--
-- `search_text` holds the indexable rendering when it differs from the
-- displayed text, and is null when it does not — which is every chunk of every
-- natively digital book. The generated vector falls back to `text`, so nothing
-- already stored changes meaning and no backfill is required.

alter table public.chunks add column search_text text;

comment on column public.chunks.search_text is
    'Indexable rendering of text with markup stripped; null when identical.';

-- A generated column''s expression cannot be altered in place, so the column
-- is replaced. Dropping it also drops the GIN index, which is recreated below.
alter table public.chunks drop column search_vector;

alter table public.chunks add column search_vector tsvector
    generated always as (
        setweight(to_tsvector('english', coalesce(section_title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(path_text, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(search_text, text, '')), 'D')
    ) stored;

create index idx_chunks_search_vector
    on public.chunks using gin (search_vector);
