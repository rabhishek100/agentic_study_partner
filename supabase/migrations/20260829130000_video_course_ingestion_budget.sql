-- One course-wide provider budget covers every new lecture job it creates.
-- Existing/reused lectures carry no new ingestion charge into the course.

alter table video.courses
    add column ingestion_cost_cap_usd numeric(10, 6) not null default 1.35
        check (ingestion_cost_cap_usd > 0),
    add column actual_ingestion_cost_usd numeric(10, 6) not null default 0
        check (
            actual_ingestion_cost_usd >= 0
            and actual_ingestion_cost_usd <= ingestion_cost_cap_usd
        );

alter table video.course_lectures
    add column ingestion_job_id uuid,
    add foreign key (ingestion_job_id, owner_id)
        references video.ingestion_jobs(id, owner_id)
        on delete set null (ingestion_job_id);

create unique index idx_video_course_lectures_ingestion_job
    on video.course_lectures (ingestion_job_id)
    where ingestion_job_id is not null;

create or replace function video.charge_course_ingestion_job()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    charged_course uuid;
begin
    if new.actual_cost_usd = old.actual_cost_usd then
        return new;
    end if;

    update video.courses as course
    set actual_ingestion_cost_usd =
            course.actual_ingestion_cost_usd
            + (new.actual_cost_usd - old.actual_cost_usd)
    from video.course_lectures as lecture
    where lecture.ingestion_job_id = new.id
      and lecture.owner_id = new.owner_id
      and course.id = lecture.course_id
      and course.owner_id = lecture.owner_id
      and course.actual_ingestion_cost_usd
            + (new.actual_cost_usd - old.actual_cost_usd)
          between 0 and course.ingestion_cost_cap_usd
    returning course.id into charged_course;

    if exists (
        select 1 from video.course_lectures as lecture
        where lecture.ingestion_job_id = new.id
          and lecture.owner_id = new.owner_id
    ) and charged_course is null then
        raise check_violation using message =
            'course ingestion cost cap would be exceeded';
    end if;
    return new;
end;
$$;

create trigger ingestion_jobs_charge_course
    after update of actual_cost_usd on video.ingestion_jobs
    for each row execute function video.charge_course_ingestion_job();

comment on column video.course_lectures.ingestion_job_id is
    'New ingestion job created by this course membership; null for reused lectures.';
comment on column video.courses.ingestion_cost_cap_usd is
    'Hard provider-cost ceiling shared by all ingestion jobs created for the course.';
