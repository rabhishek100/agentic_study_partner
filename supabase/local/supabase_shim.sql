-- The parts of a Supabase project this schema depends on, and nothing else.
--
-- `supabase start` is the supported way to get a development database, and it
-- needs Docker. When Docker is unavailable — a machine where Desktop will not
-- start, a CI runner without it — `scripts/local_postgres.sh` builds the same
-- database out of a plain Postgres 17 cluster, and this file is what makes the
-- migrations applicable to it.
--
-- Since 2026-09-03 that compatibility layer is also what the Railway
-- production database is built from, so it lives in one place —
-- `ops/postgres/bootstrap.sql` — and this file includes it rather than
-- restating it. Two copies of "the objects the migrations bind to" would
-- drift, and the direction they would drift is the dangerous one: local
-- passing while production fails partway through a replay.
--
-- `\ir` resolves relative to this file rather than to the caller's working
-- directory, so this works regardless of where psql was invoked from.

\ir ../../ops/postgres/bootstrap.sql

-- Local-only additions belong below this line. There are none today: every
-- object the local tests need is one Railway needs too. Anything added here
-- should come with a reason it is *not* wanted in production.
