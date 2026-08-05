-- Lecture questions are asked in sentences, not keywords.
--
-- The first index used the 'simple' configuration, which keeps every stopword
-- as a searchable lexeme and never stems. Combined with an AND-style query
-- that made ordinary questions — "what does he draw on the board?" — match
-- nothing at all, because no single evidence unit contains every word.
--
-- The 'english' configuration drops stopwords and stems, so the query reduces
-- to the words that carry meaning and "drawn" matches "draw". Retrieval pairs
-- this with an OR-joined query and cover-density ranking, matching the book
-- pipeline's proven lexical behavior.

drop index if exists video.idx_video_evidence_units_search;

alter table video.evidence_units drop column search_vector;

alter table video.evidence_units
    add column search_vector tsvector generated always as (
        to_tsvector('english', coalesce(retrieval_text, ''))
    ) stored;

create index idx_video_evidence_units_search
    on video.evidence_units using gin (search_vector);
