-- Reranked hybrid retrieval becomes the default for new conversations.
--
-- Measured, not assumed: the frozen gold-set comparison in
-- evaluation/retrieval_comparison_artifact.json scores hybrid-plus-reranker
-- ahead of every alternative — Recall@5 1.00 against 0.93 for hybrid alone,
-- MRR@5 0.96 against 0.85.
--
-- Existing conversations keep whatever mode they were created with; changing
-- a conversation's retrieval underneath answers it has already given would
-- make its own history unreproducible.

alter table public.conversations
    alter column retrieval_mode set default 'hybrid_rerank';
