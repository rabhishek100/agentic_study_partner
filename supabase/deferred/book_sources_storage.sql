-- Deferred until source-PDF hosting is explicitly in scope.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'book-sources',
    'book-sources',
    false,
    104857600,
    array['application/pdf']
)
on conflict (id) do update set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create policy "book_sources_owner_select"
on storage.objects for select to authenticated
using (
    bucket_id = 'book-sources'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
);

create policy "book_sources_owner_insert"
on storage.objects for insert to authenticated
with check (
    bucket_id = 'book-sources'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
);

create policy "book_sources_owner_update"
on storage.objects for update to authenticated
using (
    bucket_id = 'book-sources'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
)
with check (
    bucket_id = 'book-sources'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
);

create policy "book_sources_owner_delete"
on storage.objects for delete to authenticated
using (
    bucket_id = 'book-sources'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
);
