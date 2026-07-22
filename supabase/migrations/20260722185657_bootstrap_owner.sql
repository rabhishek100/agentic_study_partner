-- Version aligned with the hosted migration recorded by Supabase MCP.
-- Temporary single-user owner used until request-derived Supabase Auth is added.
-- This row has no password and cannot be used to log in.
insert into auth.users (id, email, raw_user_meta_data)
values (
    '00000000-0000-4000-8000-000000000001',
    'bootstrap@study-partner.local',
    '{}'::jsonb
)
on conflict (id) do nothing;
