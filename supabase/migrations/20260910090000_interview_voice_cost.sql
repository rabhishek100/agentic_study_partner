-- Keep a voice subtotal while retaining total_cost_usd as the all-provider total.
alter table public.interview_sessions
    add column voice_cost_usd numeric(12, 6) not null default 0
    check (voice_cost_usd >= 0);
