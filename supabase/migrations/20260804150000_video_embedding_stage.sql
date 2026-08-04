-- Semantic evidence embeddings become their own dependency-hashed stage.
--
-- Keeping embeddings separate from evidence construction is what makes the
-- documented invalidation rule enforceable: a changed embedding model rebuilds
-- vectors only, while canonical evidence text, decoded frames, OCR, and paid
-- visual analysis remain reused from their completed checkpoints.

alter table video.ingestion_jobs
    drop constraint ingestion_jobs_stage_check;

alter table video.ingestion_jobs
    add constraint ingestion_jobs_stage_check check (stage is null or stage in (
        'acquire_source', 'media_metadata', 'transcript', 'resources',
        'frame_selection', 'ocr', 'visual_analysis', 'spatial_regions',
        'indexing', 'embeddings', 'quality_gates', 'publish'
    ));

alter table video.ingestion_stage_checkpoints
    drop constraint ingestion_stage_checkpoints_stage_check;

alter table video.ingestion_stage_checkpoints
    add constraint ingestion_stage_checkpoints_stage_check check (stage in (
        'acquire_source', 'media_metadata', 'transcript', 'resources',
        'frame_selection', 'ocr', 'visual_analysis', 'spatial_regions',
        'indexing', 'embeddings', 'quality_gates', 'publish'
    ));

comment on table video.evidence_embeddings is
    'Rebuildable text and diagram-region vectors keyed by model, dimension, and document format version.';
