-- The parts of a Supabase project this schema depends on, and nothing else.
--
-- `supabase start` is the supported way to get a development database, and it
-- needs Docker. When Docker is unavailable — a machine where Desktop will not
-- start, a CI runner without it — `scripts/local_postgres.sh` builds the same
-- database out of a plain Postgres 17 cluster, and this file is what makes the
-- migrations applicable to it.
--
-- Scope is deliberately narrow: every object here exists because a migration
-- or a test refers to it, and nothing here reproduces Supabase behaviour the
-- tests do not exercise. In particular the row-level security policies the
-- migrations create are *created* against these tables but never *enforced*,
-- because the tests connect as the superuser, exactly as they do against a
-- real local Supabase. Owner scoping is asserted in SQL by the tests
-- themselves; RLS is a second line the hosted database draws.
--
-- What this is NOT: a way to run the application against a fake auth service.
-- There is no GoTrue here, no JWT verification, no storage API. It serves one
-- purpose — letting the migrations apply and the storage tests run.

create schema if not exists auth;
create schema if not exists storage;
create schema if not exists extensions;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end
$$;

grant usage on schema public to anon, authenticated, service_role;
grant usage on schema storage to anon, authenticated, service_role;

create table if not exists auth.users (
    id uuid primary key default gen_random_uuid(),
    email text unique,
    raw_user_meta_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

-- The owner of the current request, read from the JWT claims the connection
-- carries. Both forms, because Supabase's own definition reads both and the
-- row-level-security tests use the second: PostgREST sets the flattened
-- `request.jwt.claim.sub` on older versions and the whole claim object on
-- newer ones.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )::uuid
$$;

create table if not exists storage.buckets (
    id text primary key,
    name text not null,
    public boolean not null default false,
    file_size_limit bigint,
    allowed_mime_types text[],
    created_at timestamptz not null default now()
);

create table if not exists storage.objects (
    id uuid primary key default gen_random_uuid(),
    bucket_id text references storage.buckets(id),
    name text,
    owner uuid,
    metadata jsonb,
    created_at timestamptz not null default now()
);
alter table storage.objects enable row level security;

-- Supabase's own: the path segments of an object name, without the file.
create or replace function storage.foldername(name text)
returns text[]
language sql
immutable
as $$
  select array_remove((string_to_array(name, '/'))[1:cardinality(string_to_array(name, '/')) - 1], null)
$$;

-- `config.toml` sets `extra_search_path = ["public", "extensions"]`, which is
-- how the `vector` type is reachable unqualified. The Python client resolves
-- that type at connection time and fails outright without it.
alter database postgres set search_path = public, extensions;
