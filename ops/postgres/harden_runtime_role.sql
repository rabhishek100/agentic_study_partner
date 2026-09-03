-- The role the API and worker connect as, and the row-level security that
-- must not be left half-on around it.
--
-- Two separate jobs, in one file because doing one without the other produces
-- a broken deployment either way.
--
-- ## Least privilege
--
-- Railway hands out a superuser (`postgres`). The runtime does not need it:
-- it reads and writes rows in `public` and `video`, and inserts into the
-- `auth.users` shadow registry. It never creates a table, never installs an
-- extension, never reads another database. `app_runtime` can do exactly what
-- the application does; the superuser URL stays with the operator and out of
-- the service's variables.
--
-- ## Row-level security, and why it comes off
--
-- The migrations attach RLS policies built on `auth.uid()`, which reads JWT
-- claims that PostgREST used to set on the connection. On Railway there is no
-- PostgREST: the browser never connects to Postgres, the API does, and it
-- sets no claims. `auth.uid()` therefore returns NULL, and a policy comparing
-- `owner_id = auth.uid()` matches no rows at all.
--
-- That is the trap. `postgres` is superuser and silently bypasses RLS, so the
-- application works today and would break the moment it stopped being
-- superuser — which is the very hardening above. A least-privilege role plus
-- enforced auth.uid() policies is not a stricter deployment, it is an empty
-- library.
--
-- So the policies are dropped rather than disabled, and this is the honest
-- part: leaving them present-but-inert would read to the next person as a
-- second line of defence that does not exist. Owner scoping in this
-- deployment is enforced in application SQL — every request-serving query
-- filters on the verified JWT subject — and that is what
-- `tests/test_multi_user_isolation.py` asserts. Local Supabase keeps its
-- policies and keeps testing them, because there they are real.
--
-- Idempotent. Run after bootstrap, before pointing the runtime at the target.
--
--   psql "$OPERATOR_URL" -v ON_ERROR_STOP=1 \
--        -v runtime_password="$(generated)" -f ops/postgres/harden_runtime_role.sql

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Runtime role
-- ---------------------------------------------------------------------------
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'app_runtime') then
        create role app_runtime login;
    end if;
end
$$;

-- Set separately so the password never appears in a CREATE that might be
-- logged by a statement logger configured to record DDL.
alter role app_runtime with password :'runtime_password';
alter role app_runtime with nosuperuser nocreatedb nocreaterole noreplication nobypassrls;

grant connect on database :"DBNAME" to app_runtime;

grant usage on schema public, video, auth, extensions to app_runtime;

grant select, insert, update, delete on all tables in schema public, video to app_runtime;
grant usage, select on all sequences in schema public, video to app_runtime;

-- The shadow identity registry: the runtime inserts a row on a verified
-- caller's first request and updates the email. It never deletes an identity.
grant select, insert, update on auth.users to app_runtime;

-- Migrations run as the operator, so anything they create later must still be
-- reachable by the runtime without a second pass over this file.
alter default privileges in schema public, video
    grant select, insert, update, delete on tables to app_runtime;
alter default privileges in schema public, video
    grant usage, select on sequences to app_runtime;

-- `public` is world-creatable before PostgreSQL 15 and world-usable after.
-- Nothing here should be creating objects in it but the operator.
revoke create on schema public from public;

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------
-- Every policy on an application table, dropped, and RLS turned off with it.
-- Enumerated from the catalog rather than listed, so a policy added by a
-- future migration is handled without this file having to be remembered.
do $$
declare
    entry record;
    dropped int := 0;
    disabled int := 0;
begin
    for entry in
        select schemaname, tablename, policyname
        from pg_policies
        where schemaname in ('public', 'video')
    loop
        execute format(
            'drop policy if exists %I on %I.%I',
            entry.policyname, entry.schemaname, entry.tablename
        );
        dropped := dropped + 1;
    end loop;

    for entry in
        select n.nspname, c.relname
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where c.relkind = 'r'
          and n.nspname in ('public', 'video')
          and c.relrowsecurity
    loop
        execute format(
            'alter table %I.%I disable row level security',
            entry.nspname, entry.relname
        );
        disabled := disabled + 1;
    end loop;

    raise notice
        'dropped % policies and disabled row level security on % tables; '
        'owner scoping is enforced in application SQL and asserted by '
        'tests/test_multi_user_isolation.py',
        dropped, disabled;
end
$$;

-- ---------------------------------------------------------------------------
-- What the runtime can actually see
-- ---------------------------------------------------------------------------
do $$
declare
    readable int;
    writable int;
begin
    select count(*) into readable
      from information_schema.table_privileges
     where grantee = 'app_runtime' and privilege_type = 'SELECT';
    select count(*) into writable
      from information_schema.table_privileges
     where grantee = 'app_runtime' and privilege_type = 'INSERT';
    raise notice 'app_runtime can read % and write % tables', readable, writable;

    if exists (select 1 from pg_roles where rolname = 'app_runtime' and rolsuper) then
        raise exception 'app_runtime must not be superuser';
    end if;
    if exists (select 1 from pg_roles where rolname = 'app_runtime' and rolbypassrls) then
        raise exception 'app_runtime must not bypass row level security';
    end if;
end
$$;
