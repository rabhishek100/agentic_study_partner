-- Chapters a lecture never published, derived from what was on its screen.
--
-- `chapter_kind` allowed 'youtube' and 'manual': a list the source shipped, or
-- a list a person typed. Neither covers a list the pipeline worked out for
-- itself, and an uploaded lecture has no source list at all — which left the
-- summary workflow segmenting a hundred minutes into equal time slices and the
-- topic inventory paying a model to invent an outline the slides already
-- stated.
--
-- 'derived' is a third kind rather than a flag on the other two because a
-- reader should be able to tell where an outline came from, and because it is
-- rebuildable derived data: re-deriving deletes and replaces its own rows and
-- must never touch a list the source published or a person wrote.

alter table video.chapters
    drop constraint if exists chapters_chapter_kind_check;

alter table video.chapters
    add constraint chapters_chapter_kind_check
    check (chapter_kind in ('youtube', 'manual', 'derived'));

comment on column video.chapters.chapter_kind is
    'youtube: published by the source. manual: entered by a person. '
    'derived: computed from the lecture''s own frames, rebuildable, and '
    'replaced wholesale on every rebuild.';
