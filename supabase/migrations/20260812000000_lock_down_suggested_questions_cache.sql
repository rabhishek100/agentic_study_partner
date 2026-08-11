-- The suggested-question cache is an internal API/worker implementation
-- detail. It is never read or written directly through Supabase's Data API.
--
-- The original migration created this public-schema table without RLS, which
-- made it fail Supabase's `rls_disabled_in_public` security check. Keep the
-- table private to the privileged server-side database connection.

alter table public.suggested_questions_cache enable row level security;

revoke all privileges on table public.suggested_questions_cache from public;
revoke all privileges on table public.suggested_questions_cache
    from anon, authenticated;

comment on table public.suggested_questions_cache is
    'Server-side cache for dynamic starter prompts; direct Data API access is disabled.';
