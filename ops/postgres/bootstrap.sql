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
-- Every step is guarded by an existence check rather than written as `create
-- ... if not exists`. That is not belt-and-braces: on a real Supabase project
-- `auth` is owned by `supabase_auth_admin`, and `create table if not exists
-- auth.users` fails with "permission denied for schema auth" *before* it gets
-- as far as noticing the table is already there. Guarding on existence makes
-- this file a no-op against a managed project instead of an error, so the
-- same bootstrap can be pointed at local Supabase, a bare cluster, or Railway
-- and do the right thing in each.
--
-- Scope stays narrow: an object earns its place here only by being referenced
-- by a migration or a test. This is not a way to run the application against
-- a fake Supabase — there is no GoTrue, no PostgREST, no storage API, and the
-- row-level-security policies the migrations create are created but never
-- enforced, because nothing here sets JWT claims on a connection.

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
do $$
declare
    wanted text;
begin
    foreach wanted in array array['extensions', 'auth', 'storage', 'supabase_migrations']
    loop
        if not exists (select 1 from pg_namespace where nspname = wanted) then
            execute format('create schema %I', wanted);
        end if;
    end loop;
end
$$;

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
-- Into `extensions`, not `public`, because that is where the migrations spell
-- the types: `extensions.vector`, `extensions.halfvec`. A dump of a Supabase
-- database declares its vector columns the same way and cannot be restored
-- into a database where the type resolves anywhere else.
do $$
declare
    wanted text;
begin
    foreach wanted in array array['vector', 'pgcrypto', 'uuid-ossp']
    loop
        if not exists (select 1 from pg_extension where extname = wanted) then
            execute format('create extension %I with schema extensions', wanted);
        end if;
    end loop;
end
$$;

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
-- with NOLOGIN and are never given a password: on Railway nothing
-- authenticates as them, and the policies that reference them are not an
-- isolation mechanism here. Owner scoping is enforced in application SQL,
-- which is what the multi-user tests actually assert.
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

-- Granting requires ownership of the schema, which a managed project does not
-- hand out. Skipped there; the platform has already done it.
do $$
begin
    if pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE') then
        grant usage on schema public to anon, authenticated, service_role;
    end if;
    if pg_catalog.has_schema_privilege(current_user, 'storage', 'CREATE') then
        grant usage on schema storage to anon, authenticated, service_role;
    end if;
    if pg_catalog.has_schema_privilege(current_user, 'extensions', 'CREATE') then
        grant usage on schema extensions to anon, authenticated, service_role;
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- auth
-- ---------------------------------------------------------------------------
-- A shadow application identity registry, not GoTrue's table. Tenant rows
-- carry `owner_id` foreign keys into it, and preserving those keys is what
-- lets the corpus move providers without every table being rewritten. Rows
-- are inserted by `ensure_application_user()` after a token has been verified,
-- never by a client.
--
-- On a managed project this table is GoTrue's own, with far more columns. The
-- guard leaves it entirely alone there; the columns this application uses are
-- a subset either way.
do $$
begin
    if to_regclass('auth.users') is null then
        create table auth.users (
            id uuid primary key default gen_random_uuid(),
            email text unique,
            raw_user_meta_data jsonb not null default '{}'::jsonb,
            created_at timestamptz not null default now()
        );
    end if;
end
$$;

-- Read from the JWT claims the connection carries. Both spellings, because
-- Supabase's own definition reads both and the row-level-security tests use
-- the second. On Railway nothing sets either, so this returns NULL and the
-- policies built on it match no rows — which is why they are dropped rather
-- than relied on. See `ops/postgres/harden_runtime_role.sql`.
do $$
begin
    if not exists (
        select 1 from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'auth' and p.proname = 'uid'
    ) then
        execute $fn$
            create function auth.uid()
            returns uuid
            language sql
            stable
            as $body$
              select coalesce(
                nullif(current_setting('request.jwt.claim.sub', true), ''),
                nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
              )::uuid
            $body$
        $fn$;
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- storage
-- ---------------------------------------------------------------------------
-- Present so the historical migrations that create buckets and policies can
-- replay. After the source PDFs move to R2 nothing reads these tables at
-- runtime; they are kept because dropping them would mean editing migrations
-- that have already run.
do $$
begin
    if to_regclass('storage.buckets') is null then
        create table storage.buckets (
            id text primary key,
            name text not null,
            public boolean not null default false,
            file_size_limit bigint,
            allowed_mime_types text[],
            created_at timestamptz not null default now()
        );
    end if;

    if to_regclass('storage.objects') is null then
        create table storage.objects (
            id uuid primary key default gen_random_uuid(),
            bucket_id text references storage.buckets(id),
            name text,
            owner uuid,
            metadata jsonb,
            created_at timestamptz not null default now()
        );
        alter table storage.objects enable row level security;
    end if;
end
$$;

-- Supabase's own: the path segments of an object name, without the file.
do $$
begin
    if not exists (
        select 1 from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'storage' and p.proname = 'foldername'
    ) then
        execute $fn$
            create function storage.foldername(name text)
            returns text[]
            language sql
            immutable
            as $body$
              select array_remove((string_to_array(name, '/'))[1:cardinality(string_to_array(name, '/')) - 1], null)
            $body$
        $fn$;
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Migration ledger
-- ---------------------------------------------------------------------------
-- Same table and shape Supabase uses, so a database bootstrapped here reports
-- its head the same way a hosted one does and the audit can compare the two.
do $$
begin
    if to_regclass('supabase_migrations.schema_migrations') is null then
        create table supabase_migrations.schema_migrations (
            version text primary key,
            inserted_at timestamptz not null default now()
        );
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Search path
-- ---------------------------------------------------------------------------
-- `vector` and `halfvec` must resolve unqualified: the Python client resolves
-- those types at connection time and fails outright without it. Supabase sets
-- this through `config.toml`'s `extra_search_path`.
--
-- `current_database()` rather than a literal `postgres`, because Railway names
-- the database `railway` and the statement is not portable otherwise. Altering
-- a database needs ownership, which a managed project does not grant, so this
-- is skipped where it is already someone else's job.
do $$
begin
    if exists (
        select 1 from pg_database
        where datname = current_database()
          and pg_catalog.pg_has_role(current_user, datdba, 'USAGE')
    ) then
        execute format(
            'alter database %I set search_path = public, extensions',
            current_database()
        );
    end if;
end
$$;
