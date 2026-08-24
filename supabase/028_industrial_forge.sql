-- Phase 3 : réception autonome des minerais et transformation lazy en lingots.
-- Ordre des verrous : advisory forgeron -> profil -> transport/job -> wallet/inventaire.

create table public.industrial_blacksmiths (
  owner_discord_user_id bigint primary key references public.industrial_users(discord_user_id) on delete restrict,
  company_id bigint not null unique references public.industrial_companies(id) on delete cascade,
  forge_level integer not null default 1 check (forge_level between 1 and 20),
  speed_level integer not null default 1 check (speed_level between 1 and 20),
  storage_level integer not null default 1 check (storage_level between 1 and 20),
  yield_level integer not null default 1 check (yield_level between 1 and 20),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.industrial_forge_jobs (
  id bigserial primary key,
  owner_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  company_id bigint not null references public.industrial_companies(id) on delete restrict,
  forge_slot integer not null check (forge_slot between 1 and 20),
  resource_input text not null check (resource_input = 'iron_ore'),
  resource_output text not null check (resource_output = 'iron_ingot'),
  input_quantity bigint not null check (input_quantity between 1 and 1000000),
  output_quantity bigint not null check (output_quantity = input_quantity),
  speed_level_at_start integer not null check (speed_level_at_start between 1 and 20),
  yield_level_at_start integer not null check (yield_level_at_start between 1 and 20),
  started_at timestamptz not null,
  finishes_at timestamptz not null,
  completed_at timestamptz,
  collected_at timestamptz,
  status text not null default 'processing' check (status in ('processing', 'completed', 'collected')),
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  created_at timestamptz not null default now(),
  check (finishes_at >= started_at),
  check ((status = 'processing' and completed_at is null and collected_at is null)
      or (status = 'completed' and completed_at is not null and collected_at is null)
      or (status = 'collected' and completed_at is not null and collected_at is not null))
);

create unique index industrial_forge_jobs_active_slot_idx
  on public.industrial_forge_jobs(owner_discord_user_id, forge_slot)
  where status = 'processing';
create index industrial_forge_jobs_owner_status_idx
  on public.industrial_forge_jobs(owner_discord_user_id, status, finishes_at);

create table public.industrial_forge_upgrades (
  id bigserial primary key,
  owner_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  upgrade_type text not null check (upgrade_type in ('forges', 'speed', 'storage', 'yield')),
  previous_level integer not null check (previous_level between 1 and 19),
  new_level integer not null check (new_level = previous_level + 1 and new_level <= 20),
  cost bigint not null check (cost > 0),
  balance_after bigint not null check (balance_after >= 0),
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  created_at timestamptz not null default now()
);

create table public.industrial_forge_collections (
  id bigserial primary key,
  owner_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  collected_quantity bigint not null check (collected_quantity >= 0),
  inventory_quantity bigint not null check (inventory_quantity >= 0),
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  created_at timestamptz not null default now()
);

create or replace function public.enforce_industrial_blacksmith_ownership()
returns trigger language plpgsql set search_path = ''
as $$
declare company_owner bigint; company_job text; user_job text;
begin
  select c.owner_discord_user_id, c.job_type into company_owner, company_job
  from public.industrial_companies c where c.id = new.company_id;
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = new.owner_discord_user_id;
  if company_owner is distinct from new.owner_discord_user_id
     or company_job is distinct from 'blacksmith' or user_job is distinct from 'blacksmith' then
    raise exception 'industrial blacksmith requires matching user and company' using errcode = '23514';
  end if;
  return new;
end;
$$;

create trigger industrial_blacksmiths_enforce_ownership
before insert or update of owner_discord_user_id, company_id on public.industrial_blacksmiths
for each row execute function public.enforce_industrial_blacksmith_ownership();
create trigger industrial_blacksmiths_set_updated_at
before update on public.industrial_blacksmiths
for each row execute function public.set_industrial_updated_at();

alter table public.industrial_blacksmiths enable row level security;
alter table public.industrial_forge_jobs enable row level security;
alter table public.industrial_forge_upgrades enable row level security;
alter table public.industrial_forge_collections enable row level security;

create or replace function public.industrial_forge_rate(p_level integer)
returns bigint language sql immutable strict set search_path = ''
as $$ select floor(10::numeric * power(1.35::numeric, p_level - 1))::bigint; $$;

create or replace function public.industrial_forge_storage_capacity(p_level integer)
returns bigint language sql immutable strict set search_path = ''
as $$ select floor(500::numeric * power(1.5::numeric, p_level - 1))::bigint; $$;

create or replace function public.industrial_forge_upgrade_cost(p_upgrade_type text, p_current_level integer)
returns bigint language plpgsql immutable strict set search_path = ''
as $$
declare base_cost bigint;
begin
  base_cost := case p_upgrade_type when 'forges' then 1200 when 'speed' then 900
    when 'storage' then 600 when 'yield' then 1000 else null end;
  if base_cost is null then raise exception 'invalid forge upgrade type' using errcode = '22023'; end if;
  return floor(base_cost::numeric * power(1.8::numeric, p_current_level - 1))::bigint;
end;
$$;

create or replace function public.industrial_forge_duration_seconds(p_quantity bigint, p_speed_level integer)
returns integer language sql immutable strict set search_path = ''
as $$
  select greatest(1, ceil(p_quantity::numeric * 3600 / public.industrial_forge_rate(p_speed_level))::integer);
$$;

-- Réceptionne directement les transports visant le Forgeron, indépendamment du Marchand,
-- puis marque les jobs arrivés à échéance. Aucun timer applicatif n'est nécessaire.
create or replace function public.ensure_and_refresh_industrial_blacksmith(p_owner_discord_user_id bigint)
returns text language plpgsql security invoker set search_path = ''
as $$
declare current_job text; blacksmith_company_id bigint; v_current_time timestamptz;
  transport_row public.industrial_transports%rowtype;
begin
  select u.primary_job into current_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if current_job is distinct from 'blacksmith' then return 'not_blacksmith'; end if;
  select c.id into blacksmith_company_id from public.industrial_companies c
  where c.owner_discord_user_id = p_owner_discord_user_id
    and c.is_first_company and c.job_type = 'blacksmith' limit 1;
  if blacksmith_company_id is null then return 'no_blacksmith_company'; end if;

  perform pg_catalog.pg_advisory_xact_lock(p_owner_discord_user_id);
  insert into public.industrial_blacksmiths(owner_discord_user_id, company_id)
  values (p_owner_discord_user_id, blacksmith_company_id)
  on conflict (owner_discord_user_id) do nothing;
  perform 1 from public.industrial_blacksmiths b
  where b.owner_discord_user_id = p_owner_discord_user_id for update;

  v_current_time := clock_timestamp();
  for transport_row in
    select t.* from public.industrial_transports t
    where t.receiver_company_id = blacksmith_company_id and t.status = 'in_transit'
      and t.arrival_at <= v_current_time order by t.id for update
  loop
    update public.industrial_transports set status = 'delivered', completed_at = v_current_time
    where id = transport_row.id and status = 'in_transit';
    if found then
      insert into public.industrial_inventory(owner_discord_user_id, resource_type, quantity)
      values (p_owner_discord_user_id, transport_row.resource_type, transport_row.quantity)
      on conflict on constraint industrial_inventory_pkey do update
        set quantity = industrial_inventory.quantity + excluded.quantity;
    end if;
  end loop;

  update public.industrial_forge_jobs set status = 'completed', completed_at = v_current_time
  where owner_discord_user_id = p_owner_discord_user_id and status = 'processing'
    and finishes_at <= v_current_time;
  return 'ok';
end;
$$;

create or replace function public.get_or_create_and_refresh_industrial_blacksmith(p_owner_discord_user_id bigint)
returns table (
  result_status text, current_job text, owner_discord_user_id bigint,
  company_id bigint, company_name text, forge_level integer, speed_level integer,
  storage_level integer, yield_level integer, active_jobs integer,
  completed_jobs integer, reserved_output bigint
)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text;
begin
  operation_status := public.ensure_and_refresh_industrial_blacksmith(p_owner_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::text, null::integer, null::integer, null::integer, null::integer,
      null::integer, null::integer, null::bigint; return;
  end if;
  return query select 'ok', user_job, b.owner_discord_user_id, b.company_id, c.name,
    b.forge_level, b.speed_level, b.storage_level, b.yield_level,
    count(j.id) filter (where j.status = 'processing')::integer,
    count(j.id) filter (where j.status = 'completed')::integer,
    coalesce(sum(j.output_quantity) filter (where j.status in ('processing', 'completed')), 0)::bigint
  from public.industrial_blacksmiths b join public.industrial_companies c on c.id = b.company_id
  left join public.industrial_forge_jobs j on j.owner_discord_user_id = b.owner_discord_user_id
  where b.owner_discord_user_id = p_owner_discord_user_id
  group by b.owner_discord_user_id, b.company_id, c.name, b.forge_level,
    b.speed_level, b.storage_level, b.yield_level;
end;
$$;

create or replace function public.start_industrial_forge_job(
  p_owner_discord_user_id bigint, p_resource_type text, p_quantity bigint, p_request_id text
)
returns table (
  result_status text, current_job text, available_amount bigint, remaining_input bigint,
  id bigint, owner_discord_user_id bigint, company_id bigint, forge_slot integer,
  resource_input text, resource_output text, input_quantity bigint, output_quantity bigint,
  started_at timestamptz, finishes_at timestamptz, status text
)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text; blacksmith_row public.industrial_blacksmiths%rowtype;
  job_row public.industrial_forge_jobs%rowtype; free_slot integer; available bigint;
  reserved bigint; storage_capacity bigint; duration_seconds integer; v_current_time timestamptz;
begin
  if p_resource_type <> 'iron_ore' or p_quantity not between 1 and 1000000
     or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid forge job' using errcode = '22023';
  end if;
  operation_status := public.ensure_and_refresh_industrial_blacksmith(p_owner_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::integer, null::text, null::text,
      null::bigint, null::bigint, null::timestamptz, null::timestamptz, null::text; return;
  end if;
  select j.* into job_row from public.industrial_forge_jobs j where j.request_id = p_request_id;
  if found then
    if job_row.owner_discord_user_id <> p_owner_discord_user_id
       or job_row.resource_input <> p_resource_type or job_row.input_quantity <> p_quantity then
      raise exception 'request id reused with different forge parameters' using errcode = '23505';
    end if;
    select i.quantity into available from public.industrial_inventory i
    where i.owner_discord_user_id = p_owner_discord_user_id and i.resource_type = p_resource_type;
    return query select 'duplicate', user_job, coalesce(available, 0), coalesce(available, 0),
      job_row.id, job_row.owner_discord_user_id, job_row.company_id, job_row.forge_slot,
      job_row.resource_input, job_row.resource_output, job_row.input_quantity,
      job_row.output_quantity, job_row.started_at, job_row.finishes_at, job_row.status; return;
  end if;
  select b.* into blacksmith_row from public.industrial_blacksmiths b
  where b.owner_discord_user_id = p_owner_discord_user_id for update;
  select slot into free_slot from pg_catalog.generate_series(1, blacksmith_row.forge_level) as slots(slot)
  where not exists (select 1 from public.industrial_forge_jobs j
    where j.owner_discord_user_id = p_owner_discord_user_id
      and j.forge_slot = slot and j.status = 'processing') order by slot limit 1;
  if free_slot is null then
    return query select 'no_forge_available', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::integer, null::text, null::text,
      null::bigint, null::bigint, null::timestamptz, null::timestamptz, null::text; return;
  end if;
  select coalesce(sum(j.output_quantity), 0)::bigint into reserved
  from public.industrial_forge_jobs j where j.owner_discord_user_id = p_owner_discord_user_id
    and j.status in ('processing', 'completed');
  storage_capacity := public.industrial_forge_storage_capacity(blacksmith_row.storage_level);
  if reserved + p_quantity > storage_capacity then
    return query select 'storage_full', user_job, storage_capacity - reserved, null::bigint,
      null::bigint, null::bigint, null::bigint, null::integer, null::text, null::text,
      null::bigint, null::bigint, null::timestamptz, null::timestamptz, null::text; return;
  end if;
  select i.quantity into available from public.industrial_inventory i
  where i.owner_discord_user_id = p_owner_discord_user_id and i.resource_type = p_resource_type for update;
  available := coalesce(available, 0);
  if available < p_quantity then
    return query select 'insufficient_inventory', user_job, available, null::bigint,
      null::bigint, null::bigint, null::bigint, null::integer, null::text, null::text,
      null::bigint, null::bigint, null::timestamptz, null::timestamptz, null::text; return;
  end if;
  update public.industrial_inventory as target_inventory
  set quantity = target_inventory.quantity - p_quantity
  where target_inventory.owner_discord_user_id = p_owner_discord_user_id
    and target_inventory.resource_type = p_resource_type;
  duration_seconds := public.industrial_forge_duration_seconds(p_quantity, blacksmith_row.speed_level);
  v_current_time := clock_timestamp();
  insert into public.industrial_forge_jobs(
    owner_discord_user_id, company_id, forge_slot, resource_input, resource_output,
    input_quantity, output_quantity, speed_level_at_start, yield_level_at_start,
    started_at, finishes_at, request_id
  ) values (p_owner_discord_user_id, blacksmith_row.company_id, free_slot,
    'iron_ore', 'iron_ingot', p_quantity, p_quantity, blacksmith_row.speed_level,
    blacksmith_row.yield_level, v_current_time,
    v_current_time + pg_catalog.make_interval(secs => duration_seconds), p_request_id)
  returning * into job_row;
  return query select 'ok', user_job, available - p_quantity, available - p_quantity,
    job_row.id, job_row.owner_discord_user_id, job_row.company_id, job_row.forge_slot,
    job_row.resource_input, job_row.resource_output, job_row.input_quantity,
    job_row.output_quantity, job_row.started_at, job_row.finishes_at, job_row.status;
end;
$$;

create or replace function public.collect_industrial_forge_jobs(
  p_owner_discord_user_id bigint, p_request_id text
)
returns table (
  result_status text, current_job text, collected_quantity bigint, inventory_quantity bigint
)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text; previous public.industrial_forge_collections%rowtype;
  collected bigint; inventory_total bigint; v_current_time timestamptz;
begin
  if p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid forge collection request' using errcode = '22023';
  end if;
  operation_status := public.ensure_and_refresh_industrial_blacksmith(p_owner_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint; return;
  end if;
  select c.* into previous from public.industrial_forge_collections c where c.request_id = p_request_id;
  if found then
    if previous.owner_discord_user_id <> p_owner_discord_user_id then
      raise exception 'collection request belongs to another user' using errcode = '23505';
    end if;
    return query select 'duplicate', user_job, previous.collected_quantity, previous.inventory_quantity; return;
  end if;
  perform 1 from public.industrial_forge_jobs j
  where j.owner_discord_user_id = p_owner_discord_user_id and j.status = 'completed'
  order by j.id for update;
  select coalesce(sum(j.output_quantity), 0)::bigint into collected
  from public.industrial_forge_jobs j
  where j.owner_discord_user_id = p_owner_discord_user_id and j.status = 'completed';
  v_current_time := clock_timestamp();
  if collected > 0 then
    insert into public.industrial_inventory(owner_discord_user_id, resource_type, quantity)
    values (p_owner_discord_user_id, 'iron_ingot', collected)
    on conflict on constraint industrial_inventory_pkey do update
      set quantity = industrial_inventory.quantity + excluded.quantity
    returning quantity into inventory_total;
    update public.industrial_forge_jobs set status = 'collected', collected_at = v_current_time
    where owner_discord_user_id = p_owner_discord_user_id and status = 'completed';
  else
    select i.quantity into inventory_total from public.industrial_inventory i
    where i.owner_discord_user_id = p_owner_discord_user_id and i.resource_type = 'iron_ingot';
    inventory_total := coalesce(inventory_total, 0);
  end if;
  insert into public.industrial_forge_collections(
    owner_discord_user_id, collected_quantity, inventory_quantity, request_id
  ) values (p_owner_discord_user_id, collected, inventory_total, p_request_id);
  return query select 'ok', user_job, collected, inventory_total;
end;
$$;

create or replace function public.upgrade_industrial_forge(
  p_owner_discord_user_id bigint, p_upgrade_type text, p_request_id text
)
returns table (
  result_status text, current_job text, owner_discord_user_id bigint, company_id bigint,
  company_name text, forge_level integer, speed_level integer, storage_level integer,
  yield_level integer, active_jobs integer, completed_jobs integer, reserved_output bigint,
  upgrade_type text, previous_level integer, new_level integer,
  upgrade_cost bigint, wallet_balance bigint
)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text; blacksmith_row public.industrial_blacksmiths%rowtype;
  previous public.industrial_forge_upgrades%rowtype; company_title text; old_level integer;
  cost_value bigint; balance_value bigint; active_count integer; completed_count integer; reserved bigint;
begin
  if p_upgrade_type not in ('forges', 'speed', 'storage', 'yield')
     or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid forge upgrade' using errcode = '22023';
  end if;
  operation_status := public.ensure_and_refresh_industrial_blacksmith(p_owner_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint, null::text,
      null::integer, null::integer, null::integer, null::integer, null::integer,
      null::integer, null::bigint, p_upgrade_type, null::integer, null::integer,
      null::bigint, null::bigint; return;
  end if;
  select u.* into previous from public.industrial_forge_upgrades u where u.request_id = p_request_id;
  if found and (previous.owner_discord_user_id <> p_owner_discord_user_id
                or previous.upgrade_type <> p_upgrade_type) then
    raise exception 'request id reused with different forge upgrade' using errcode = '23505';
  end if;
  select b.* into blacksmith_row from public.industrial_blacksmiths b
  where b.owner_discord_user_id = p_owner_discord_user_id for update;
  select c.name into company_title from public.industrial_companies c where c.id = blacksmith_row.company_id;
  select count(*) filter (where status = 'processing')::integer,
    count(*) filter (where status = 'completed')::integer,
    coalesce(sum(output_quantity) filter (where status in ('processing', 'completed')), 0)::bigint
  into active_count, completed_count, reserved from public.industrial_forge_jobs
  where owner_discord_user_id = p_owner_discord_user_id;
  if previous.id is not null then
    return query select 'duplicate', user_job, blacksmith_row.owner_discord_user_id,
      blacksmith_row.company_id, company_title, blacksmith_row.forge_level,
      blacksmith_row.speed_level, blacksmith_row.storage_level, blacksmith_row.yield_level,
      active_count, completed_count, reserved, previous.upgrade_type,
      previous.previous_level, previous.new_level, previous.cost, previous.balance_after; return;
  end if;
  old_level := case p_upgrade_type when 'forges' then blacksmith_row.forge_level
    when 'speed' then blacksmith_row.speed_level when 'storage' then blacksmith_row.storage_level
    when 'yield' then blacksmith_row.yield_level end;
  select u.credits into balance_value from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id for update;
  if old_level >= 20 then
    return query select 'max_level', user_job, null::bigint, null::bigint, null::text,
      null::integer, null::integer, null::integer, null::integer, null::integer,
      null::integer, null::bigint, p_upgrade_type, old_level, old_level,
      null::bigint, balance_value; return;
  end if;
  cost_value := public.industrial_forge_upgrade_cost(p_upgrade_type, old_level);
  if balance_value < cost_value then
    return query select 'insufficient_funds', user_job, null::bigint, null::bigint, null::text,
      null::integer, null::integer, null::integer, null::integer, null::integer,
      null::integer, null::bigint, p_upgrade_type, old_level, old_level,
      cost_value, balance_value; return;
  end if;
  update public.industrial_users set credits = credits - cost_value
  where discord_user_id = p_owner_discord_user_id;
  update public.industrial_blacksmiths as target_blacksmith set
    forge_level = forge_level + case when p_upgrade_type = 'forges' then 1 else 0 end,
    speed_level = speed_level + case when p_upgrade_type = 'speed' then 1 else 0 end,
    storage_level = storage_level + case when p_upgrade_type = 'storage' then 1 else 0 end,
    yield_level = yield_level + case when p_upgrade_type = 'yield' then 1 else 0 end
  where target_blacksmith.owner_discord_user_id = p_owner_discord_user_id returning * into blacksmith_row;
  balance_value := balance_value - cost_value;
  insert into public.industrial_forge_upgrades(
    owner_discord_user_id, upgrade_type, previous_level, new_level, cost, balance_after, request_id
  ) values (p_owner_discord_user_id, p_upgrade_type, old_level, old_level + 1,
    cost_value, balance_value, p_request_id);
  return query select 'ok', user_job, blacksmith_row.owner_discord_user_id,
    blacksmith_row.company_id, company_title, blacksmith_row.forge_level,
    blacksmith_row.speed_level, blacksmith_row.storage_level, blacksmith_row.yield_level,
    active_count, completed_count, reserved, p_upgrade_type, old_level,
    old_level + 1, cost_value, balance_value;
end;
$$;

revoke all on table public.industrial_blacksmiths, public.industrial_forge_jobs,
  public.industrial_forge_upgrades, public.industrial_forge_collections
  from public, anon, authenticated;
revoke all on sequence public.industrial_forge_jobs_id_seq,
  public.industrial_forge_upgrades_id_seq, public.industrial_forge_collections_id_seq
  from public, anon, authenticated;
grant select, insert, update on table public.industrial_blacksmiths,
  public.industrial_forge_jobs, public.industrial_forge_upgrades,
  public.industrial_forge_collections to service_role;
grant usage, select on sequence public.industrial_forge_jobs_id_seq,
  public.industrial_forge_upgrades_id_seq, public.industrial_forge_collections_id_seq
  to service_role;

revoke all on function public.enforce_industrial_blacksmith_ownership() from public, anon, authenticated;
revoke all on function public.industrial_forge_rate(integer) from public, anon, authenticated;
revoke all on function public.industrial_forge_storage_capacity(integer) from public, anon, authenticated;
revoke all on function public.industrial_forge_upgrade_cost(text,integer) from public, anon, authenticated;
revoke all on function public.industrial_forge_duration_seconds(bigint,integer) from public, anon, authenticated;
revoke all on function public.ensure_and_refresh_industrial_blacksmith(bigint) from public, anon, authenticated;
revoke all on function public.get_or_create_and_refresh_industrial_blacksmith(bigint) from public, anon, authenticated;
revoke all on function public.start_industrial_forge_job(bigint,text,bigint,text) from public, anon, authenticated;
revoke all on function public.collect_industrial_forge_jobs(bigint,text) from public, anon, authenticated;
revoke all on function public.upgrade_industrial_forge(bigint,text,text) from public, anon, authenticated;

grant execute on function public.industrial_forge_rate(integer) to service_role;
grant execute on function public.industrial_forge_storage_capacity(integer) to service_role;
grant execute on function public.industrial_forge_upgrade_cost(text,integer) to service_role;
grant execute on function public.industrial_forge_duration_seconds(bigint,integer) to service_role;
grant execute on function public.ensure_and_refresh_industrial_blacksmith(bigint) to service_role;
grant execute on function public.get_or_create_and_refresh_industrial_blacksmith(bigint) to service_role;
grant execute on function public.start_industrial_forge_job(bigint,text,bigint,text) to service_role;
grant execute on function public.collect_industrial_forge_jobs(bigint,text) to service_role;
grant execute on function public.upgrade_industrial_forge(bigint,text,text) to service_role;
