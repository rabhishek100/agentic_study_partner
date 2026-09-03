-- Embeddings move to float16, and video text vectors to 1024 dimensions.
--
-- Measured on the frozen gold sets rather than assumed, recall against the
-- current float32/3072 behaviour:
--
--   halfvec, same dimensions   video 0.912 -> 0.912   book 0.931 -> 0.931
--   halfvec at 1024 dimensions video 0.912 -> 0.897   book 0.931 -> 0.889
--
-- So float16 is free on both sides: identical anchors, identical nodes, on
-- every query in both sets. Truncation is not free, and it is nearly three
-- times more expensive on books (-4.5%) than on video (-1.6%), which is why
-- only video takes it. Video embeddings are also the ones that grow — two of
-- twenty-one lectures account for the current 136 MB, and the full course
-- projects past a gigabyte — while the book corpus is effectively static at
-- 97 MB. Paying 4.5% to reclaim 58 MB is the wrong trade; paying 1.6% to
-- reclaim roughly 600 MB is not.
--
-- `text-embedding-3-large` is trained with Matryoshka representation
-- learning, so a stored 3072-vector truncated to its first 1024 components
-- approximates the natively-1024 embedding, and cosine ranking is unchanged
-- by the renormalisation it skips. Existing vectors are therefore converted
-- in place and nothing is re-embedded: this migration buys no model calls.

-- Books: float16, same 3072 dimensions.
alter table public.chunk_embeddings
    alter column embedding type extensions.halfvec using embedding::extensions.halfvec;

-- Video: float16, and text vectors truncated to 1024. Image vectors are a
-- different model at 768 and are left exactly as they are.
alter table video.evidence_embeddings
    alter column embedding type extensions.halfvec using embedding::extensions.halfvec;

update video.evidence_embeddings
set embedding = ((embedding::extensions.vector::real[])[1:1024])
                ::extensions.vector(1024)::extensions.halfvec,
    dimension = 1024
where embedding_kind = 'text' and dimension = 3072;

-- The check was written when 3072 was the only possibility. It is now one of
-- two, and the column itself is what records which.
alter table public.chunk_embeddings
    drop constraint if exists chunk_embeddings_dimension_check;
alter table public.chunk_embeddings
    add constraint chunk_embeddings_dimension_check
    check (dimension in (1024, 3072));

comment on column public.chunk_embeddings.embedding is
    'float16. Identical retrieval to float32 on the gold set, at half the bytes.';
comment on column video.evidence_embeddings.embedding is
    'float16. Text vectors are 1024-dimension Matryoshka truncations; image vectors stay 768.';
