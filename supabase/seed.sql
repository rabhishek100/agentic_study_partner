-- Stable owner for bootstrap mode and deterministic fixtures. The versioned
-- bootstrap migration creates the same row in hosted Postgres; this remains
-- idempotent for local resets.
-- This row has no password; create login-capable users through Supabase Auth.
insert into auth.users (id, email, raw_user_meta_data)
values (
    '00000000-0000-4000-8000-000000000001',
    'bootstrap@study-partner.local',
    '{}'::jsonb
)
on conflict (id) do nothing;
