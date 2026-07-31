-- Keep the existing private source bucket aligned with the API and worker.
-- Updating a deployed bucket needs a forward migration; changing the original
-- creation migration would only affect fresh environments.
update storage.buckets
set file_size_limit = 104857600
where id = 'book-sources';
