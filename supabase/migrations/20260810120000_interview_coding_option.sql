-- Persist whether a candidate explicitly requested a coding exercise.

alter table public.interview_sessions
    add column if not exists coding_exercise_requested boolean not null default false;

comment on column public.interview_sessions.coding_exercise_requested is
    'Guarantees one source-grounded Python exercise before the interview proceeds normally.';
