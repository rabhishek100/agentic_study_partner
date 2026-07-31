-- Lower the bucket to the ceiling Supabase actually enforces.
--
-- A bucket's file_size_limit cannot lift the project-wide upload limit, which
-- on the free plan is 50 MB. Raising this bucket to 100 MiB therefore changed
-- nothing at the platform: a 91 MiB upload still passed the browser check,
-- passed the API, reserved a job, and was rejected by Storage with a bare 413
-- after minutes of transfer.
--
-- Every layer now states the same number, so an oversized file is refused
-- before it is read. Raise this together with the project setting, not alone.
update storage.buckets
set file_size_limit = 52428800
where id = 'book-sources';
