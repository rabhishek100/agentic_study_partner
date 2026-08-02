-- A readable copy of a book, for books whose own bytes cannot be stored.
--
-- The reading pane needs a PDF in Storage so it can open the page an answer
-- cites. Supabase caps uploads at 50 MB on the free plan, and the scans that
-- most need a reading pane are the ones that exceed it: one in this library is
-- 55.5 MB. It arrived through the operator ingest path, which takes Storage
-- out of the loop entirely, so nothing was stored and the pane had nothing to
-- open.
--
-- The obvious shortcut - re-encode it smaller and store that as the source -
-- would break something real. `books.file_hash` identifies the exact bytes the
-- canonical content was derived from, and `scripts/restore_book_source.py`
-- matches a file to its book by that hash and never by name. A re-encode has a
-- different hash, so storing one as the source would silently make every book
-- unrestorable and every future integrity check a lie.
--
-- So the viewer copy is its own thing. `source_*` continues to mean "the bytes
-- that were ingested, hash-identified by file_hash". `viewer_*` means "what a
-- reader is shown", which may be a compressed rendering and is derived,
-- rebuildable, and never authoritative for anything.

alter table public.books
    add column viewer_storage_bucket text,
    add column viewer_storage_path text,
    add constraint books_viewer_storage_pair check (
        (viewer_storage_bucket is null and viewer_storage_path is null)
        or (viewer_storage_bucket is not null and viewer_storage_path is not null)
    );

comment on column public.books.viewer_storage_path is
    'Derived readable copy for the reading pane; not the ingested bytes. '
    'Falls back to source_storage_path when null.';
