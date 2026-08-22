-- Owner-local daily card reminders and persistent in-app notifications.
-- Existing users remain opted out; saving reminder preferences schedules the
-- first future occurrence explicitly through the API.

alter table public.deck_preferences
    add column if not exists review_reminder_enabled boolean not null default false,
    add column if not exists review_reminder_time time without time zone
        not null default time '19:00',
    add column if not exists review_timezone text not null default 'UTC'
        check (btrim(review_timezone) <> '' and length(review_timezone) <= 100),
    add column if not exists next_review_reminder_at timestamptz;

create index if not exists idx_deck_preferences_due_reminder
    on public.deck_preferences (next_review_reminder_at, owner_id)
    where review_reminder_enabled and next_review_reminder_at is not null;

-- Reminder scheduling is a server-owned state machine. The original decks
-- migration granted direct writes to the whole preferences row; revoke those
-- writes now that the dedicated owner-scoped API is the only supported
-- mutation boundary. Reads remain available for ordinary authenticated users.
revoke insert, update, delete on public.deck_preferences from authenticated;
grant select on public.deck_preferences to authenticated;

create table public.notifications (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    kind text not null check (kind in ('daily_cards_review')),
    dedupe_key text not null check (btrim(dedupe_key) <> ''),
    local_date date not null,
    title text not null check (btrim(title) <> ''),
    body text not null check (btrim(body) <> ''),
    href text not null check (
        left(href, 1) = '/' and left(href, 2) <> '//'
    ),
    payload_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    read_at timestamptz,
    dismissed_at timestamptz,

    unique (id, owner_id),
    unique (owner_id, kind, dedupe_key),
    constraint notifications_payload_is_an_object
        check (jsonb_typeof(payload_json) = 'object')
);

create index idx_notifications_owner_active
    on public.notifications (owner_id, created_at desc, id desc)
    where dismissed_at is null;

alter table public.notifications enable row level security;

create policy notifications_owner_read on public.notifications
    for select to authenticated
    using (owner_id = (select auth.uid()));

-- Notifications are created only by the server-side reminder reconciler.
-- Acknowledgement also goes through the owner-scoped API; direct clients
-- cannot forge, delete, or rewrite a reminder into another kind of event.
grant select on public.notifications to authenticated;

comment on table public.notifications is
    'Persistent owner-scoped in-app events. Delivery surfaces are derived from these rows.';
comment on column public.notifications.dedupe_key is
    'Stable event identity; daily card reminders use the owner-local YYYY-MM-DD.';
