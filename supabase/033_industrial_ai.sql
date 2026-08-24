-- Phase 8 : entreprises IA sans faux compte Discord, évaluées lazy sur 30 jours.
create table public.industrial_user_activity(
 discord_user_id bigint primary key references public.industrial_users(discord_user_id) on delete cascade,
 last_active_at timestamptz not null default now(),command_count bigint not null default 1 check(command_count>0)
);
create table public.industrial_ai_companies(
 id bigserial primary key,name text not null check(char_length(btrim(name)) between 3 and 40),
 job_type text not null unique check(job_type in('miner','merchant','blacksmith')),
 is_ai boolean not null default true check(is_ai),enabled boolean not null default true,
 efficiency_percent integer not null default 60 check(efficiency_percent between 1 and 99),
 created_at timestamptz not null default now(),updated_at timestamptz not null default now()
);
alter table public.industrial_user_activity enable row level security;
alter table public.industrial_ai_companies enable row level security;

create or replace function public.record_industrial_activity(p_discord_user_id bigint)
returns table(result_status text) language plpgsql security invoker set search_path=''
as $$begin
 insert into public.industrial_users(discord_user_id)values(p_discord_user_id)
  on conflict(discord_user_id)do nothing;
 insert into public.industrial_user_activity(discord_user_id)values(p_discord_user_id)
 on conflict(discord_user_id)do update set last_active_at=clock_timestamp(),command_count=industrial_user_activity.command_count+1;
 return query select 'ok';end;$$;

create or replace function public.evaluate_industrial_ai_companies()
returns table(id bigint,name text,job_type text,enabled boolean,efficiency_percent integer,active_players bigint)
language plpgsql security invoker set search_path=''
as $$declare selected_job text;active_count bigint;ai_name text;begin
 perform pg_catalog.pg_advisory_xact_lock(9000000002);
 foreach selected_job in array array['miner','merchant','blacksmith'] loop
  select count(*) into active_count from public.industrial_users u join public.industrial_user_activity a
   on a.discord_user_id=u.discord_user_id where u.primary_job=selected_job
    and a.last_active_at>=now()-interval '30 days';
  ai_name:=case selected_job when 'miner' then 'Mines de Secours' when 'merchant' then 'Transit de Secours'
    else 'Forges de Secours' end;
  insert into public.industrial_ai_companies(name,job_type,enabled)
   values(ai_name,selected_job,active_count<2) on conflict(job_type)do update
   set enabled=excluded.enabled,updated_at=clock_timestamp();
 end loop;
 return query select a.id,a.name,a.job_type,a.enabled,a.efficiency_percent,
  (select count(*) from public.industrial_users u join public.industrial_user_activity x
   on x.discord_user_id=u.discord_user_id where u.primary_job=a.job_type
    and x.last_active_at>=now()-interval '30 days')::bigint
  from public.industrial_ai_companies a order by a.job_type;
end;$$;

revoke all on table public.industrial_user_activity,public.industrial_ai_companies from public,anon,authenticated;
revoke all on sequence public.industrial_ai_companies_id_seq from public,anon,authenticated;
grant select,insert,update on table public.industrial_user_activity,public.industrial_ai_companies to service_role;
grant usage,select on sequence public.industrial_ai_companies_id_seq to service_role;
revoke all on function public.record_industrial_activity(bigint),public.evaluate_industrial_ai_companies() from public,anon,authenticated;
grant execute on function public.record_industrial_activity(bigint),public.evaluate_industrial_ai_companies() to service_role;
