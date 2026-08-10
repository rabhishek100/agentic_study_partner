-- Structured candidate code is canonical interview history. Python executes
-- only in the browser; this column stores the submitted source and reported
-- result alongside the existing spoken/text answer.

alter table public.interview_turns
    add column coding_answer_json jsonb;

alter table public.interview_turns
    add constraint interview_coding_answer_is_object check (
        coding_answer_json is null
        or jsonb_typeof(coding_answer_json) = 'object'
    );
