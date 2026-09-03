-- The parts of a Supabase project this schema depends on, on any Postgres 17.
--
-- Every migration in `supabase/migrations/` was written against a Supabase
-- database, so they refer to objects the platform provides rather than the
-- repository: the `extensions` schema, `auth.users`, `auth.uid()`,
-- `storage.objects`, the three PostgREST roles. A plain Postgres 17 cluster
-- has none of them, and replaying the migrations against one fails on the
-- first reference.
--
-- This file is that compatibility layer, and it is deliberately the *only*
-- copy of it. `supabase/local/supabase_shim.sql` includes this file rather
-- than restating it, so the Docker-less development database and the Railway
-- production database cannot drift apart in the objects the migrations bind
-- to. Adding an object here because Railway needs it therefore also adds it
-- to local, which is the point.
--
-- Scope stays narrow: an object earns its place here only by being referenced
-- by a migration or a test. This is not a way to run the application against
-- a fake Supabase — there is no GoTrue, no PostgREST, no storage API, and the
-- row-level-security policies the migrations create are created but never
-- enforced, because nothing here sets JWT claims on a connection.
--
-- Idempotent by construction: it is applied on every bootstrap, before the
-- migration runner decides what is new.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Version floor
-- ---------------------------------------------------------------------------
-- The dumps this database is loaded from are PostgreSQL 17 custom-format
-- archives, and `halfvec` needs pgvector 0.7. Failing here is far cheaper
-- than failing three hours into a restore.
do $$
begin
    if current_setting('server_version_num')::int < 170000 then
        raise exception
            'PostgreSQL 17 or newer is required, found %',
            current_setting('server_version');
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Compatibility schemas
-- ---------------------------------------------------------------------------
create schema if not exists extensions;
create schema if not exists auth;
create schema if not exists storage;
create schema if not exists supabase_migrations;

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
-- Into `extensions`, not `public`, because that is where the migrations spell
-- the types: `extensions.vector`, `extensions.halfvec`. A dump of a Supabase
-- database declares its vector columns the same way and cannot be restored
-- into a database where the type resolves anywhere else.
create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;
create extension if not exists "uuid-ossp" with schema extensions;

do $$
declare
    installed text;
begin
    select extversion into installed from pg_extension where extname = 'vector';
    -- `halfvec` arrived in pgvector 0.7.0. Every embedding column in this
    -- schema is one, so an older pgvector does not produce a degraded
    -- database, it produces a failed migration partway through.
    if not exists (select 1 from pg_type where typname = 'halfvec') then
        raise exception
            'pgvector % does not provide halfvec; 0.7.0 or newer is required',
            coalesce(installed, '(absent)');
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------
-- The migrations grant to these three and attach row-level-security policies
-- naming them, so they must exist for the replay to succeed. They are created
-- with NOLOGIN and are never given a password: on Railway nothing authenticates
-- as them, and the policies that reference them are not an isolation mechanism
-- here. Owner scoping is enforced in application SQL, which is what
-- `docs/deployment.md` and the multi-user tests actually assert.
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
grant usage on schema extensions to anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- auth
-- ---------------------------------------------------------------------------
-- A shadow application identity registry, not GoTrue's table. Tenant rows
-- carry `owner_id` foreign keys into it, and preserving those keys is what
-- lets the corpus move providers without every table being rewritten. Rows
-- are inserted by `ensure_application_user()` after a token has been verified,
-- never by a client.
create table if not exists auth.users (
    id uuid primary key default gen_random_uuid(),
    email text unique,
    raw_user_meta_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

-- Read from the JWT claims the connection carries. Both spellings, because
-- Supabase's own definition reads both and the row-level-security tests use
-- the second. On Railway nothing sets either, so this returns NULL and the
-- policies built on it match no rows — which is why they are dropped rather
-- than relied on. See `ops/postgres/harden_runtime_role.sql`.
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

-- ---------------------------------------------------------------------------
-- storage
-- ---------------------------------------------------------------------------
-- Present so the historical migrations that create buckets and policies can
-- replay. After the source PDFs move to R2 nothing reads these tables at
-- runtime; they are kept because dropping them would mean editing migrations
-- that have already run.
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

-- ---------------------------------------------------------------------------
-- Migration ledger
-- ---------------------------------------------------------------------------
-- Same table and shape Supabase uses, so a database bootstrapped here reports
-- its head the same way a hosted one does and the audit can compare the two.
create table if not exists supabase_migrations.schema_migrations (
    version text primary key,
    inserted_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Search path
-- ---------------------------------------------------------------------------
-- `vector` and `halfvec` must resolve unqualified: the Python client resolves
-- those types at connection time and fails outright without it. Supabase sets
-- this through `config.toml`'s `extra_search_path`.
--
-- `current_database()` rather than a literal `postgres`, because Railway names
-- the database `railway` and the statement is not portable otherwise. This is
-- the one thing here that cannot be written without dynamic SQL.
do $$
begin
    execute format(
        'alter database %I set search_path = public, extensions',
        current_database()
    );
end
$$;
