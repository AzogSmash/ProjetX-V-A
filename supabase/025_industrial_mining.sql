-- Mine V1 lazy : aucune boucle background, production calculée à l'interaction.

create table if not exists industrial_mines (
  owner_discord_user_id bigint primary key
    references public.industrial_users(discord_user_id) on delete restrict,
  company_id bigint not null unique
    references public.industrial_companies(id) on delete cascade,
  resource_type text not null default 'iron_ore'
    check (resource_type in ('iron_ore')),
  stock bigint not null default 0 check (stock >= 0),
  storage_level integer not null default 1 check (storage_level between 1 and 20),
  production_level integer not null default 1 check (production_level between 1 and 20),
  quality_level integer not null default 1 check (quality_level between 1 and 20),
  -- Accumulateur entier « taux horaire × secondes », toujours dans [0, 3599].
  production_progress bigint not null default 0
    check (production_progress between 0 and 3599),
  last_production_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists industrial_inventory (
  owner_discord_user_id bigint not null
    references public.industrial_users(discord_user_id) on delete restrict,
  resource_type text not null,
  quantity bigint not null default 0 check (quantity >= 0),
  updated_at timestamptz not null default now(),
  primary key (owner_discord_user_id, resource_type)
);

create or replace function enforce_industrial_mine_ownership()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  company_owner bigint;
  company_job text;
  user_job text;
begin
  select c.owner_discord_user_id, c.job_type
  into company_owner, company_job
  from public.industrial_companies as c
  where c.id = new.company_id;

  select u.primary_job into user_job
  from public.industrial_users as u
  where u.discord_user_id = new.owner_discord_user_id;

  if company_owner is distinct from new.owner_discord_user_id
     or company_job is distinct from 'miner'
     or user_job is distinct from 'miner' then
    raise exception 'industrial mine requires matching miner user and company'
      using errcode = '23514';
  end if;
  return new;
end;
$$;

revoke all on function public.enforce_industrial_mine_ownership()
  from public, anon, authenticated;

drop trigger if exists industrial_mines_enforce_ownership on industrial_mines;
create trigger industrial_mines_enforce_ownership
before insert or update of owner_discord_user_id, company_id
on industrial_mines
for each row execute function public.enforce_industrial_mine_ownership();

drop trigger if exists industrial_mines_set_updated_at on industrial_mines;
create trigger industrial_mines_set_updated_at
before update on industrial_mines
for each row execute function public.set_industrial_updated_at();

drop trigger if exists industrial_inventory_set_updated_at on industrial_inventory;
create trigger industrial_inventory_set_updated_at
before update on industrial_inventory
for each row execute function public.set_industrial_updated_at();

alter table industrial_mines enable row level security;
alter table industrial_inventory enable row level security;

create or replace function industrial_mine_production_rate(p_level integer)
returns bigint
language sql
immutable
strict
set search_path = ''
as $$
  select floor(10::numeric * power(1.35::numeric, p_level - 1))::bigint;
$$;

create or replace function industrial_mine_storage_capacity(p_level integer)
returns bigint
language sql
immutable
strict
set search_path = ''
as $$
  select floor(100::numeric * power(1.5::numeric, p_level - 1))::bigint;
$$;

create or replace function industrial_mine_upgrade_cost(
  p_upgrade_type text,
  p_current_level integer
)
returns bigint
language plpgsql
immutable
strict
set search_path = ''
as $$
declare
  base_cost bigint;
begin
  base_cost := case p_upgrade_type
    when 'storage' then 250
    when 'production' then 400
    when 'quality' then 500
    else null
  end;
  if base_cost is null then
    raise exception 'invalid mine upgrade type' using errcode = '22023';
  end if;
  return floor(base_cost::numeric * power(1.8::numeric, p_current_level - 1))::bigint;
end;
$$;

-- Appel interne commun. Le verrou advisory reste détenu jusqu'à la fin de la
-- transaction appelante ; la ligne mine est également verrouillée FOR UPDATE.
create or replace function ensure_and_refresh_industrial_mine(
  p_owner_discord_user_id bigint
)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  current_job text;
  miner_company_id bigint;
  mine_row public.industrial_mines%rowtype;
  v_current_time timestamptz;
  elapsed_seconds bigint;
  production_rate bigint;
  storage_capacity bigint;
  progress_total bigint;
  produced bigint;
begin
  select u.primary_job into current_job
  from public.industrial_users as u
  where u.discord_user_id = p_owner_discord_user_id;

  if current_job is distinct from 'miner' then
    return 'not_miner';
  end if;

  select c.id into miner_company_id
  from public.industrial_companies as c
  where c.owner_discord_user_id = p_owner_discord_user_id
    and c.is_first_company
    and c.job_type = 'miner'
  limit 1;

  if miner_company_id is null then
    return 'no_miner_company';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(p_owner_discord_user_id);

  insert into public.industrial_mines (owner_discord_user_id, company_id)
  values (p_owner_discord_user_id, miner_company_id)
  on conflict (owner_discord_user_id) do nothing;

  select m.* into mine_row
  from public.industrial_mines as m
  where m.owner_discord_user_id = p_owner_discord_user_id
  for update;

  v_current_time := clock_timestamp();
  elapsed_seconds := greatest(
    0,
    floor(extract(epoch from (v_current_time - mine_row.last_production_at)))::bigint
  );
  production_rate := public.industrial_mine_production_rate(mine_row.production_level);
  storage_capacity := public.industrial_mine_storage_capacity(mine_row.storage_level);
  progress_total := mine_row.production_progress + elapsed_seconds * production_rate;
  produced := progress_total / 3600;

  if mine_row.stock >= storage_capacity
     or mine_row.stock + produced >= storage_capacity then
    update public.industrial_mines
    set stock = storage_capacity,
        production_progress = 0,
        last_production_at = v_current_time
    where owner_discord_user_id = p_owner_discord_user_id;
  else
    update public.industrial_mines
    set stock = stock + produced,
        production_progress = mod(progress_total, 3600),
        last_production_at = v_current_time
    where owner_discord_user_id = p_owner_discord_user_id;
  end if;

  return 'ok';
end;
$$;

create or replace function get_or_create_and_refresh_industrial_mine(
  p_owner_discord_user_id bigint
)
returns table (
  result_status text,
  current_job text,
  owner_discord_user_id bigint,
  company_id bigint,
  company_name text,
  resource_type text,
  stock bigint,
  storage_level integer,
  production_level integer,
  quality_level integer,
  production_progress bigint,
  last_production_at timestamptz
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  operation_status text;
  user_job text;
begin
  operation_status := public.ensure_and_refresh_industrial_mine(p_owner_discord_user_id);
  select u.primary_job into user_job
  from public.industrial_users as u
  where u.discord_user_id = p_owner_discord_user_id;

  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::text, null::text, null::bigint, null::integer, null::integer,
      null::integer, null::bigint, null::timestamptz;
    return;
  end if;

  return query
  select 'ok'::text, user_job, m.owner_discord_user_id, m.company_id,
    c.name, m.resource_type, m.stock, m.storage_level, m.production_level,
    m.quality_level, m.production_progress, m.last_production_at
  from public.industrial_mines as m
  join public.industrial_companies as c on c.id = m.company_id
  where m.owner_discord_user_id = p_owner_discord_user_id;
end;
$$;

create or replace function collect_industrial_mine(p_owner_discord_user_id bigint)
returns table (
  result_status text,
  current_job text,
  owner_discord_user_id bigint,
  company_id bigint,
  company_name text,
  resource_type text,
  stock bigint,
  storage_level integer,
  production_level integer,
  quality_level integer,
  production_progress bigint,
  last_production_at timestamptz,
  collected_quantity bigint,
  inventory_quantity bigint
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  operation_status text;
  user_job text;
  mine_row public.industrial_mines%rowtype;
  company_title text;
  new_inventory_quantity bigint;
begin
  operation_status := public.ensure_and_refresh_industrial_mine(p_owner_discord_user_id);
  select u.primary_job into user_job
  from public.industrial_users as u
  where u.discord_user_id = p_owner_discord_user_id;

  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::text, null::text, null::bigint, null::integer, null::integer,
      null::integer, null::bigint, null::timestamptz, null::bigint, null::bigint;
    return;
  end if;

  select m.* into mine_row
  from public.industrial_mines as m
  where m.owner_discord_user_id = p_owner_discord_user_id
  for update;
  select c.name into company_title
  from public.industrial_companies as c
  where c.id = mine_row.company_id;

  insert into public.industrial_inventory (owner_discord_user_id, resource_type, quantity)
  values (p_owner_discord_user_id, mine_row.resource_type, mine_row.stock)
  on conflict on constraint industrial_inventory_pkey do update
    set quantity = industrial_inventory.quantity + excluded.quantity
  returning quantity into new_inventory_quantity;

  update public.industrial_mines as target_mine
  set stock = 0
  where target_mine.owner_discord_user_id = p_owner_discord_user_id;

  return query select 'ok'::text, user_job, mine_row.owner_discord_user_id,
    mine_row.company_id, company_title, mine_row.resource_type, 0::bigint,
    mine_row.storage_level, mine_row.production_level, mine_row.quality_level,
    mine_row.production_progress, mine_row.last_production_at, mine_row.stock,
    new_inventory_quantity;
end;
$$;

create or replace function upgrade_industrial_mine(
  p_owner_discord_user_id bigint,
  p_upgrade_type text
)
returns table (
  result_status text,
  current_job text,
  owner_discord_user_id bigint,
  company_id bigint,
  company_name text,
  resource_type text,
  stock bigint,
  storage_level integer,
  production_level integer,
  quality_level integer,
  production_progress bigint,
  last_production_at timestamptz,
  upgrade_type text,
  previous_level integer,
  new_level integer,
  upgrade_cost bigint,
  wallet_balance bigint
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  operation_status text;
  user_job text;
  mine_row public.industrial_mines%rowtype;
  company_title text;
  old_level integer;
  calculated_cost bigint;
  current_balance bigint;
begin
  if p_upgrade_type not in ('storage', 'production', 'quality') then
    raise exception 'invalid mine upgrade type' using errcode = '22023';
  end if;

  operation_status := public.ensure_and_refresh_industrial_mine(p_owner_discord_user_id);
  select u.primary_job into user_job
  from public.industrial_users as u
  where u.discord_user_id = p_owner_discord_user_id;

  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::text, null::text, null::bigint, null::integer, null::integer,
      null::integer, null::bigint, null::timestamptz, p_upgrade_type,
      null::integer, null::integer, null::bigint, null::bigint;
    return;
  end if;

  select m.* into mine_row
  from public.industrial_mines as m
  where m.owner_discord_user_id = p_owner_discord_user_id
  for update;
  select c.name into company_title
  from public.industrial_companies as c
  where c.id = mine_row.company_id;

  old_level := case p_upgrade_type
    when 'storage' then mine_row.storage_level
    when 'production' then mine_row.production_level
    when 'quality' then mine_row.quality_level
  end;

  select u.credits into current_balance
  from public.industrial_users as u
  where u.discord_user_id = p_owner_discord_user_id
  for update;

  if old_level >= 20 then
    return query select 'max_level'::text, user_job, mine_row.owner_discord_user_id,
      mine_row.company_id, company_title, mine_row.resource_type, mine_row.stock,
      mine_row.storage_level, mine_row.production_level, mine_row.quality_level,
      mine_row.production_progress, mine_row.last_production_at, p_upgrade_type,
      old_level, old_level, null::bigint, current_balance;
    return;
  end if;

  calculated_cost := public.industrial_mine_upgrade_cost(p_upgrade_type, old_level);
  if current_balance < calculated_cost then
    return query select 'insufficient_funds'::text, user_job,
      mine_row.owner_discord_user_id, mine_row.company_id, company_title,
      mine_row.resource_type, mine_row.stock, mine_row.storage_level,
      mine_row.production_level, mine_row.quality_level,
      mine_row.production_progress, mine_row.last_production_at, p_upgrade_type,
      old_level, old_level, calculated_cost, current_balance;
    return;
  end if;

  update public.industrial_users
  set credits = credits - calculated_cost
  where discord_user_id = p_owner_discord_user_id;

  update public.industrial_mines as target_mine
  set storage_level = case when p_upgrade_type = 'storage' then storage_level + 1 else storage_level end,
      production_level = case when p_upgrade_type = 'production' then production_level + 1 else production_level end,
      quality_level = case when p_upgrade_type = 'quality' then quality_level + 1 else quality_level end
  where target_mine.owner_discord_user_id = p_owner_discord_user_id
  returning * into mine_row;

  current_balance := current_balance - calculated_cost;
  return query select 'ok'::text, user_job, mine_row.owner_discord_user_id,
    mine_row.company_id, company_title, mine_row.resource_type, mine_row.stock,
    mine_row.storage_level, mine_row.production_level, mine_row.quality_level,
    mine_row.production_progress, mine_row.last_production_at, p_upgrade_type,
    old_level, old_level + 1, calculated_cost, current_balance;
end;
$$;

revoke all on function public.enforce_industrial_mine_ownership()
  from public, anon, authenticated;
revoke all on function public.industrial_mine_production_rate(integer)
  from public, anon, authenticated;
revoke all on function public.industrial_mine_storage_capacity(integer)
  from public, anon, authenticated;
revoke all on function public.industrial_mine_upgrade_cost(text, integer)
  from public, anon, authenticated;
revoke all on function public.ensure_and_refresh_industrial_mine(bigint)
  from public, anon, authenticated;
revoke all on function public.get_or_create_and_refresh_industrial_mine(bigint)
  from public, anon, authenticated;
revoke all on function public.collect_industrial_mine(bigint)
  from public, anon, authenticated;
revoke all on function public.upgrade_industrial_mine(bigint, text)
  from public, anon, authenticated;

grant execute on function public.industrial_mine_production_rate(integer) to service_role;
grant execute on function public.industrial_mine_storage_capacity(integer) to service_role;
grant execute on function public.industrial_mine_upgrade_cost(text, integer) to service_role;
grant execute on function public.ensure_and_refresh_industrial_mine(bigint) to service_role;
grant execute on function public.get_or_create_and_refresh_industrial_mine(bigint) to service_role;
grant execute on function public.collect_industrial_mine(bigint) to service_role;
grant execute on function public.upgrade_industrial_mine(bigint, text) to service_role;
