-- Take row-level security back off after a migration adds it.
--
-- Every migration in this repository attaches RLS policies built on
-- `auth.uid()`, because on local Supabase they are real and tested. On the
-- Railway deployment they are not: there is no PostgREST setting JWT claims on
-- the connection, so `auth.uid()` is NULL and a policy comparing
-- `owner_id = auth.uid()` matches no rows. The runtime role is `nobypassrls`,
-- so a table that arrives with RLS enabled is not a stricter table — it is an
-- empty one.
--
-- `harden_runtime_role.sql` already does this, enumerated from the catalog so
-- that a future migration is handled without anyone remembering this file. But
-- it also rotates the `app_runtime` password, which would cut off every
-- running service unless the current password is passed back in. That makes it
-- the wrong tool for the routine case: a migration landed, and the tables it
-- created need the same posture as every other table.
--
-- So this is the RLS half on its own, idempotent and safe to run after any
-- migration. Grants need no equivalent: `harden_runtime_role.sql` set default
-- privileges for the operator role, so tables a later migration creates are
-- already reachable by `app_runtime`.
--
-- Owner scoping in this deployment is enforced in application SQL — every
-- request-serving query filters on the verified JWT subject — and that is what
-- `tests/test_multi_user_isolation.py` asserts.
--
--   psql "$OPERATOR_URL" -v ON_ERROR_STOP=1 -f ops/postgres/disable_inert_rls.sql

\set ON_ERROR_STOP on

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
        'dropped % policies and disabled row level security on % tables',
        dropped, disabled;
end
$$;
