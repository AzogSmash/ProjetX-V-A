-- Économie industrielle indépendante du système coins/casino historique.

create table if not exists industrial_users (
  discord_user_id bigint primary key,
  credits bigint not null default 0 check (credits >= 0),
  primary_job text null check (
    primary_job is null
    or primary_job in ('miner', 'merchant', 'blacksmith', 'banker')
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists industrial_companies (
  id bigserial primary key,
  owner_discord_user_id bigint not null
    references industrial_users(discord_user_id) on delete restrict,
  name text not null check (
    char_length(btrim(name)) between 3 and 40
    and name = btrim(name)
    and name !~ '[[:cntrl:]]'
    and position('<@' in name) = 0
    and position('<#' in name) = 0
    and position('@everyone' in lower(name)) = 0
    and position('@here' in lower(name)) = 0
  ),
  job_type text not null check (
    job_type in ('miner', 'merchant', 'blacksmith', 'banker')
  ),
  level integer not null default 1 check (level >= 1),
  is_first_company boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists industrial_companies_owner_idx
  on industrial_companies(owner_discord_user_id);

-- Une seule « première entreprise » par propriétaire, y compris sous concurrence.
create unique index if not exists industrial_companies_one_first_per_owner_idx
  on industrial_companies(owner_discord_user_id)
  where is_first_company;

create or replace function set_industrial_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

revoke all on function public.set_industrial_updated_at() from public, anon, authenticated;

drop trigger if exists industrial_users_set_updated_at on industrial_users;
create trigger industrial_users_set_updated_at
before update on industrial_users
for each row execute function public.set_industrial_updated_at();

drop trigger if exists industrial_companies_set_updated_at on industrial_companies;
create trigger industrial_companies_set_updated_at
before update on industrial_companies
for each row execute function public.set_industrial_updated_at();

create or replace function enforce_first_company_job_consistency()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  owner_primary_job text;
begin
  if new.is_first_company then
    select u.primary_job into owner_primary_job
    from public.industrial_users as u
    where u.discord_user_id = new.owner_discord_user_id;

    if owner_primary_job is null or owner_primary_job <> new.job_type then
      raise exception 'first company job must match owner primary job'
        using errcode = '23514';
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.enforce_first_company_job_consistency()
  from public, anon, authenticated;

drop trigger if exists industrial_companies_enforce_job on industrial_companies;
create trigger industrial_companies_enforce_job
before insert or update of owner_discord_user_id, job_type, is_first_company
on industrial_companies
for each row execute function public.enforce_first_company_job_consistency();

create or replace function enforce_industrial_primary_job_lock()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.primary_job is not null and new.primary_job is distinct from old.primary_job then
    raise exception 'industrial primary job is locked' using errcode = '23514';
  end if;
  return new;
end;
$$;

revoke all on function public.enforce_industrial_primary_job_lock()
  from public, anon, authenticated;

drop trigger if exists industrial_users_lock_primary_job on industrial_users;
create trigger industrial_users_lock_primary_job
before update of primary_job on industrial_users
for each row execute function public.enforce_industrial_primary_job_lock();

alter table industrial_users enable row level security;
alter table industrial_companies enable row level security;

-- Le bot utilise SUPABASE_SERVICE_ROLE_KEY. Aucune policy publique n'est créée.
create or replace function get_or_create_industrial_user(p_discord_user_id bigint)
returns setof public.industrial_users
language plpgsql
security invoker
set search_path = ''
as $$
begin
  insert into public.industrial_users (discord_user_id)
  values (p_discord_user_id)
  on conflict (discord_user_id) do nothing;

  return query
  select u.*
  from public.industrial_users as u
  where u.discord_user_id = p_discord_user_id;
end;
$$;

create or replace function create_first_industrial_company(
  p_owner_discord_user_id bigint,
  p_name text,
  p_job_type text
)
returns table (
  result_status text,
  id bigint,
  owner_discord_user_id bigint,
  name text,
  job_type text,
  level integer,
  is_first_company boolean
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  existing_company public.industrial_companies%rowtype;
  created_company public.industrial_companies%rowtype;
  current_job text;
begin
  if p_job_type not in ('miner', 'merchant', 'blacksmith', 'banker') then
    raise exception 'invalid industrial job type' using errcode = '22023';
  end if;
  if p_name is null or char_length(btrim(p_name)) not between 3 and 40 then
    raise exception 'invalid industrial company name' using errcode = '22023';
  end if;
  if p_name ~ '[[:cntrl:]]'
     or position('<@' in p_name) > 0
     or position('<#' in p_name) > 0
     or position('@everyone' in lower(p_name)) > 0
     or position('@here' in lower(p_name)) > 0 then
    raise exception 'invalid industrial company name' using errcode = '22023';
  end if;

  -- Sérialise toutes les créations pour un même Discord ID durant la transaction.
  perform pg_catalog.pg_advisory_xact_lock(p_owner_discord_user_id);

  insert into public.industrial_users (discord_user_id)
  values (p_owner_discord_user_id)
  on conflict (discord_user_id) do nothing;

  select u.primary_job into current_job
  from public.industrial_users as u
  where u.discord_user_id = p_owner_discord_user_id;

  select c.* into existing_company
  from public.industrial_companies as c
  where c.owner_discord_user_id = p_owner_discord_user_id
    and c.is_first_company
  limit 1;

  if found then
    return query select
      'already_exists'::text,
      existing_company.id,
      existing_company.owner_discord_user_id,
      existing_company.name,
      existing_company.job_type,
      existing_company.level,
      existing_company.is_first_company;
    return;
  end if;

  if current_job is not null and current_job <> p_job_type then
    raise exception 'industrial primary job is locked' using errcode = '23514';
  end if;

  update public.industrial_users
  set primary_job = p_job_type
  where discord_user_id = p_owner_discord_user_id
    and primary_job is null;

  insert into public.industrial_companies (
    owner_discord_user_id,
    name,
    job_type,
    is_first_company
  ) values (
    p_owner_discord_user_id,
    btrim(p_name),
    p_job_type,
    true
  )
  returning * into created_company;

  return query select
    'created'::text,
    created_company.id,
    created_company.owner_discord_user_id,
    created_company.name,
    created_company.job_type,
    created_company.level,
    created_company.is_first_company;
end;
$$;

revoke all on function public.get_or_create_industrial_user(bigint)
  from public, anon, authenticated;
revoke all on function public.create_first_industrial_company(bigint, text, text)
  from public, anon, authenticated;
grant execute on function public.get_or_create_industrial_user(bigint) to service_role;
grant execute on function public.create_first_industrial_company(bigint, text, text) to service_role;
