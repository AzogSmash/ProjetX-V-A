-- Phase 2 : entreprise Marchand, flotte et transports lazy vers les Forgerons.
-- Ordre des verrous : advisory marchand -> profil -> transport -> wallet/inventaire.

create table public.industrial_merchants (
  owner_discord_user_id bigint primary key
    references public.industrial_users(discord_user_id) on delete restrict,
  company_id bigint not null unique
    references public.industrial_companies(id) on delete cascade,
  truck_count integer not null default 1 check (truck_count between 1 and 20),
  truck_capacity_level integer not null default 1 check (truck_capacity_level between 1 and 20),
  truck_speed_level integer not null default 1 check (truck_speed_level between 1 and 20),
  warehouse_level integer not null default 1 check (warehouse_level between 1 and 20),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.industrial_transports (
  id bigserial primary key,
  sender_company_id bigint not null references public.industrial_companies(id) on delete restrict,
  receiver_company_id bigint not null references public.industrial_companies(id) on delete restrict,
  merchant_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  transport_type text not null default 'ore_to_blacksmith'
    check (transport_type in ('ore_to_blacksmith')),
  resource_type text not null check (resource_type in ('iron_ore')),
  quantity bigint not null check (quantity between 1 and 1000000),
  departure_at timestamptz not null,
  arrival_at timestamptz not null,
  original_duration_seconds integer not null check (original_duration_seconds between 900 and 3600),
  current_duration_seconds integer not null check (current_duration_seconds between 0 and 3600),
  status text not null default 'in_transit' check (status in ('in_transit', 'delivered')),
  truck_slot integer not null check (truck_slot between 1 and 20),
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  check (sender_company_id <> receiver_company_id),
  check (arrival_at >= departure_at),
  check ((status = 'in_transit' and completed_at is null)
      or (status = 'delivered' and completed_at is not null))
);

create unique index industrial_transports_active_truck_idx
  on public.industrial_transports(merchant_discord_user_id, truck_slot)
  where status = 'in_transit';
create index industrial_transports_merchant_time_idx
  on public.industrial_transports(merchant_discord_user_id, created_at desc);
create index industrial_transports_receiver_status_idx
  on public.industrial_transports(receiver_company_id, status, arrival_at);

create table public.industrial_merchant_upgrades (
  id bigserial primary key,
  owner_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  upgrade_type text not null check (upgrade_type in ('trucks', 'capacity', 'speed', 'warehouse')),
  previous_level integer not null check (previous_level between 1 and 19),
  new_level integer not null check (new_level = previous_level + 1 and new_level <= 20),
  cost bigint not null check (cost > 0),
  balance_after bigint not null check (balance_after >= 0),
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  created_at timestamptz not null default now()
);

create or replace function public.enforce_industrial_merchant_ownership()
returns trigger language plpgsql set search_path = ''
as $$
declare company_owner bigint; company_job text; user_job text;
begin
  select c.owner_discord_user_id, c.job_type into company_owner, company_job
  from public.industrial_companies c where c.id = new.company_id;
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = new.owner_discord_user_id;
  if company_owner is distinct from new.owner_discord_user_id
     or company_job is distinct from 'merchant' or user_job is distinct from 'merchant' then
    raise exception 'industrial merchant requires matching merchant user and company'
      using errcode = '23514';
  end if;
  return new;
end;
$$;

create trigger industrial_merchants_enforce_ownership
before insert or update of owner_discord_user_id, company_id on public.industrial_merchants
for each row execute function public.enforce_industrial_merchant_ownership();
create trigger industrial_merchants_set_updated_at
before update on public.industrial_merchants
for each row execute function public.set_industrial_updated_at();

alter table public.industrial_merchants enable row level security;
alter table public.industrial_transports enable row level security;
alter table public.industrial_merchant_upgrades enable row level security;

create or replace function public.industrial_truck_capacity(p_level integer)
returns bigint language sql immutable strict set search_path = ''
as $$ select floor(100::numeric * power(1.5::numeric, p_level - 1))::bigint; $$;

create or replace function public.industrial_trip_duration_seconds(p_level integer)
returns integer language sql immutable strict set search_path = ''
as $$
  select greatest(900, floor(3600::numeric * power(0.90::numeric, p_level - 1))::integer);
$$;

create or replace function public.industrial_warehouse_capacity(p_level integer)
returns bigint language sql immutable strict set search_path = ''
as $$ select floor(1000::numeric * power(1.5::numeric, p_level - 1))::bigint; $$;

create or replace function public.industrial_merchant_upgrade_cost(
  p_upgrade_type text, p_current_level integer
)
returns bigint language plpgsql immutable strict set search_path = ''
as $$
declare base_cost bigint;
begin
  base_cost := case p_upgrade_type when 'trucks' then 1000 when 'capacity' then 600
    when 'speed' then 800 when 'warehouse' then 500 else null end;
  if base_cost is null then
    raise exception 'invalid merchant upgrade type' using errcode = '22023';
  end if;
  return floor(base_cost::numeric * power(1.8::numeric, p_current_level - 1))::bigint;
end;
$$;

-- Appel interne : crée le profil puis livre exactement une fois les transports arrivés.
create or replace function public.ensure_and_refresh_industrial_merchant(
  p_owner_discord_user_id bigint
)
returns text language plpgsql security invoker set search_path = ''
as $$
declare current_job text; merchant_company_id bigint; transport_row record;
  receiver_owner bigint; v_current_time timestamptz;
begin
  select u.primary_job into current_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if current_job is distinct from 'merchant' then return 'not_merchant'; end if;

  select c.id into merchant_company_id from public.industrial_companies c
  where c.owner_discord_user_id = p_owner_discord_user_id
    and c.is_first_company and c.job_type = 'merchant' limit 1;
  if merchant_company_id is null then return 'no_merchant_company'; end if;

  perform pg_catalog.pg_advisory_xact_lock(p_owner_discord_user_id);
  insert into public.industrial_merchants(owner_discord_user_id, company_id)
  values (p_owner_discord_user_id, merchant_company_id)
  on conflict (owner_discord_user_id) do nothing;
  perform 1 from public.industrial_merchants m
  where m.owner_discord_user_id = p_owner_discord_user_id for update;

  v_current_time := clock_timestamp();
  for transport_row in
    select t.* from public.industrial_transports t
    where t.merchant_discord_user_id = p_owner_discord_user_id
      and t.status = 'in_transit' and t.arrival_at <= v_current_time
    order by t.id for update
  loop
    select c.owner_discord_user_id into receiver_owner
    from public.industrial_companies c where c.id = transport_row.receiver_company_id;
    insert into public.industrial_inventory(owner_discord_user_id, resource_type, quantity)
    values (receiver_owner, transport_row.resource_type, transport_row.quantity)
    on conflict on constraint industrial_inventory_pkey do update
      set quantity = industrial_inventory.quantity + excluded.quantity;
    update public.industrial_transports set status = 'delivered', completed_at = v_current_time
    where id = transport_row.id and status = 'in_transit';
  end loop;
  return 'ok';
end;
$$;

create or replace function public.get_or_create_and_refresh_industrial_merchant(
  p_owner_discord_user_id bigint
)
returns table (
  result_status text, current_job text, owner_discord_user_id bigint,
  company_id bigint, company_name text, truck_count integer,
  truck_capacity_level integer, truck_speed_level integer,
  warehouse_level integer, active_transports integer
)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text;
begin
  operation_status := public.ensure_and_refresh_industrial_merchant(p_owner_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::text, null::integer, null::integer, null::integer, null::integer, null::integer;
    return;
  end if;
  return query select 'ok'::text, user_job, m.owner_discord_user_id, m.company_id,
    c.name, m.truck_count, m.truck_capacity_level, m.truck_speed_level,
    m.warehouse_level, count(t.id)::integer
  from public.industrial_merchants m
  join public.industrial_companies c on c.id = m.company_id
  left join public.industrial_transports t on t.merchant_discord_user_id = m.owner_discord_user_id
    and t.status = 'in_transit'
  where m.owner_discord_user_id = p_owner_discord_user_id
  group by m.owner_discord_user_id, m.company_id, c.name, m.truck_count,
    m.truck_capacity_level, m.truck_speed_level, m.warehouse_level;
end;
$$;

create or replace function public.upgrade_industrial_merchant(
  p_owner_discord_user_id bigint, p_upgrade_type text, p_request_id text
)
returns table (
  result_status text, current_job text, owner_discord_user_id bigint,
  company_id bigint, company_name text, truck_count integer,
  truck_capacity_level integer, truck_speed_level integer, warehouse_level integer,
  active_transports integer, upgrade_type text, previous_level integer,
  new_level integer, upgrade_cost bigint, wallet_balance bigint
)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text; merchant_row public.industrial_merchants%rowtype;
  company_title text; old_level integer; calculated_cost bigint; current_balance bigint;
  previous_upgrade public.industrial_merchant_upgrades%rowtype; active_count integer;
begin
  if p_upgrade_type not in ('trucks', 'capacity', 'speed', 'warehouse')
     or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid merchant upgrade' using errcode = '22023';
  end if;
  operation_status := public.ensure_and_refresh_industrial_merchant(p_owner_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::text, null::integer, null::integer, null::integer, null::integer,
      null::integer, p_upgrade_type, null::integer, null::integer, null::bigint, null::bigint; return;
  end if;

  select u.* into previous_upgrade from public.industrial_merchant_upgrades u
  where u.request_id = p_request_id;
  if found then
    if previous_upgrade.owner_discord_user_id <> p_owner_discord_user_id
       or previous_upgrade.upgrade_type <> p_upgrade_type then
      raise exception 'request id reused with different upgrade parameters' using errcode = '23505';
    end if;
  end if;

  select m.* into merchant_row from public.industrial_merchants m
  where m.owner_discord_user_id = p_owner_discord_user_id for update;
  select c.name into company_title from public.industrial_companies c where c.id = merchant_row.company_id;
  select count(*)::integer into active_count from public.industrial_transports t
  where t.merchant_discord_user_id = p_owner_discord_user_id and t.status = 'in_transit';
  if found and previous_upgrade.id is not null then
    return query select 'duplicate', user_job, merchant_row.owner_discord_user_id,
      merchant_row.company_id, company_title, merchant_row.truck_count,
      merchant_row.truck_capacity_level, merchant_row.truck_speed_level,
      merchant_row.warehouse_level, active_count, previous_upgrade.upgrade_type,
      previous_upgrade.previous_level, previous_upgrade.new_level,
      previous_upgrade.cost, previous_upgrade.balance_after; return;
  end if;

  old_level := case p_upgrade_type when 'trucks' then merchant_row.truck_count
    when 'capacity' then merchant_row.truck_capacity_level
    when 'speed' then merchant_row.truck_speed_level
    when 'warehouse' then merchant_row.warehouse_level end;
  select u.credits into current_balance from public.industrial_users u
  where u.discord_user_id = p_owner_discord_user_id for update;
  if old_level >= 20 then
    return query select 'max_level', user_job, null::bigint, null::bigint,
      null::text, null::integer, null::integer, null::integer, null::integer,
      null::integer, p_upgrade_type, old_level, old_level, null::bigint, current_balance; return;
  end if;
  calculated_cost := public.industrial_merchant_upgrade_cost(p_upgrade_type, old_level);
  if current_balance < calculated_cost then
    return query select 'insufficient_funds', user_job, null::bigint, null::bigint,
      null::text, null::integer, null::integer, null::integer, null::integer,
      null::integer, p_upgrade_type, old_level, old_level, calculated_cost, current_balance; return;
  end if;
  update public.industrial_users set credits = credits - calculated_cost
  where discord_user_id = p_owner_discord_user_id;
  update public.industrial_merchants as target_merchant set
    truck_count = truck_count + case when p_upgrade_type = 'trucks' then 1 else 0 end,
    truck_capacity_level = truck_capacity_level + case when p_upgrade_type = 'capacity' then 1 else 0 end,
    truck_speed_level = truck_speed_level + case when p_upgrade_type = 'speed' then 1 else 0 end,
    warehouse_level = warehouse_level + case when p_upgrade_type = 'warehouse' then 1 else 0 end
  where target_merchant.owner_discord_user_id = p_owner_discord_user_id returning * into merchant_row;
  current_balance := current_balance - calculated_cost;
  insert into public.industrial_merchant_upgrades(
    owner_discord_user_id, upgrade_type, previous_level, new_level, cost, balance_after, request_id
  ) values (p_owner_discord_user_id, p_upgrade_type, old_level, old_level + 1,
    calculated_cost, current_balance, p_request_id);
  return query select 'ok', user_job, merchant_row.owner_discord_user_id,
    merchant_row.company_id, company_title, merchant_row.truck_count,
    merchant_row.truck_capacity_level, merchant_row.truck_speed_level,
    merchant_row.warehouse_level, active_count, p_upgrade_type, old_level,
    old_level + 1, calculated_cost, current_balance;
end;
$$;

create or replace function public.start_industrial_merchant_transport(
  p_merchant_discord_user_id bigint, p_receiver_discord_user_id bigint,
  p_resource_type text, p_quantity bigint, p_request_id text
)
returns table (
  result_status text, current_job text, available_amount bigint, id bigint,
  sender_company_id bigint, receiver_company_id bigint, receiver_company_name text,
  merchant_discord_user_id bigint, resource_type text, quantity bigint,
  departure_at timestamptz, arrival_at timestamptz, status text, truck_slot integer
)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text; merchant_row public.industrial_merchants%rowtype;
  receiver_company bigint; receiver_name text; free_slot integer; available bigint;
  capacity bigint; duration_seconds integer; v_current_time timestamptz;
  transport_row public.industrial_transports%rowtype; existing_receiver_owner bigint;
begin
  if p_resource_type <> 'iron_ore' or p_quantity not between 1 and 1000000
     or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid merchant transport' using errcode = '22023';
  end if;
  operation_status := public.ensure_and_refresh_industrial_merchant(p_merchant_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
  where u.discord_user_id = p_merchant_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::text, null::bigint, null::text,
      null::bigint, null::timestamptz, null::timestamptz, null::text, null::integer; return;
  end if;
  select t.* into transport_row from public.industrial_transports t where t.request_id = p_request_id;
  if found then
    select c.owner_discord_user_id into existing_receiver_owner
    from public.industrial_companies c where c.id = transport_row.receiver_company_id;
    if transport_row.merchant_discord_user_id <> p_merchant_discord_user_id
       or existing_receiver_owner <> p_receiver_discord_user_id
       or transport_row.resource_type <> p_resource_type or transport_row.quantity <> p_quantity then
      raise exception 'request id reused with different transport parameters' using errcode = '23505';
    end if;
    select c.name into receiver_name from public.industrial_companies c where c.id = transport_row.receiver_company_id;
    return query select 'duplicate', user_job, null::bigint, transport_row.id,
      transport_row.sender_company_id, transport_row.receiver_company_id, receiver_name,
      transport_row.merchant_discord_user_id, transport_row.resource_type,
      transport_row.quantity, transport_row.departure_at, transport_row.arrival_at,
      transport_row.status, transport_row.truck_slot; return;
  end if;

  select c.id, c.name into receiver_company, receiver_name
  from public.industrial_companies c join public.industrial_users u
    on u.discord_user_id = c.owner_discord_user_id and u.primary_job = 'blacksmith'
  where c.owner_discord_user_id = p_receiver_discord_user_id
    and c.is_first_company and c.job_type = 'blacksmith' limit 1;
  if receiver_company is null then
    return query select 'invalid_receiver', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::text, null::bigint, null::text,
      null::bigint, null::timestamptz, null::timestamptz, null::text, null::integer; return;
  end if;

  select m.* into merchant_row from public.industrial_merchants m
  where m.owner_discord_user_id = p_merchant_discord_user_id for update;
  capacity := public.industrial_truck_capacity(merchant_row.truck_capacity_level);
  if p_quantity > capacity then
    return query select 'capacity_exceeded', user_job, capacity, null::bigint,
      null::bigint, null::bigint, null::text, null::bigint, null::text,
      null::bigint, null::timestamptz, null::timestamptz, null::text, null::integer; return;
  end if;
  select slot into free_slot
  from pg_catalog.generate_series(1, merchant_row.truck_count) as slots(slot)
  where not exists (select 1 from public.industrial_transports t
    where t.merchant_discord_user_id = p_merchant_discord_user_id
      and t.truck_slot = slot and t.status = 'in_transit') order by slot limit 1;
  if free_slot is null then
    return query select 'no_truck_available', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::text, null::bigint, null::text,
      null::bigint, null::timestamptz, null::timestamptz, null::text, null::integer; return;
  end if;
  select i.quantity into available from public.industrial_inventory i
  where i.owner_discord_user_id = p_merchant_discord_user_id
    and i.resource_type = p_resource_type for update;
  available := coalesce(available, 0);
  if available < p_quantity then
    return query select 'insufficient_inventory', user_job, available, null::bigint,
      null::bigint, null::bigint, null::text, null::bigint, null::text,
      null::bigint, null::timestamptz, null::timestamptz, null::text, null::integer; return;
  end if;
  update public.industrial_inventory as target_inventory
  set quantity = target_inventory.quantity - p_quantity
  where target_inventory.owner_discord_user_id = p_merchant_discord_user_id
    and target_inventory.resource_type = p_resource_type;
  duration_seconds := public.industrial_trip_duration_seconds(merchant_row.truck_speed_level);
  v_current_time := clock_timestamp();
  insert into public.industrial_transports(
    sender_company_id, receiver_company_id, merchant_discord_user_id,
    resource_type, quantity, departure_at, arrival_at,
    original_duration_seconds, current_duration_seconds, truck_slot, request_id
  ) values (merchant_row.company_id, receiver_company, p_merchant_discord_user_id,
    p_resource_type, p_quantity, v_current_time,
    v_current_time + pg_catalog.make_interval(secs => duration_seconds),
    duration_seconds, duration_seconds, free_slot, p_request_id)
  returning * into transport_row;
  return query select 'ok', user_job, available - p_quantity, transport_row.id,
    transport_row.sender_company_id, transport_row.receiver_company_id, receiver_name,
    transport_row.merchant_discord_user_id, transport_row.resource_type,
    transport_row.quantity, transport_row.departure_at, transport_row.arrival_at,
    transport_row.status, transport_row.truck_slot;
end;
$$;

revoke all on table public.industrial_merchants, public.industrial_transports,
  public.industrial_merchant_upgrades from public, anon, authenticated;
revoke all on sequence public.industrial_transports_id_seq,
  public.industrial_merchant_upgrades_id_seq from public, anon, authenticated;
grant select, insert, update on table public.industrial_merchants,
  public.industrial_transports, public.industrial_merchant_upgrades to service_role;
grant usage, select on sequence public.industrial_transports_id_seq,
  public.industrial_merchant_upgrades_id_seq to service_role;

revoke all on function public.enforce_industrial_merchant_ownership() from public, anon, authenticated;
revoke all on function public.industrial_truck_capacity(integer) from public, anon, authenticated;
revoke all on function public.industrial_trip_duration_seconds(integer) from public, anon, authenticated;
revoke all on function public.industrial_warehouse_capacity(integer) from public, anon, authenticated;
revoke all on function public.industrial_merchant_upgrade_cost(text,integer) from public, anon, authenticated;
revoke all on function public.ensure_and_refresh_industrial_merchant(bigint) from public, anon, authenticated;
revoke all on function public.get_or_create_and_refresh_industrial_merchant(bigint) from public, anon, authenticated;
revoke all on function public.upgrade_industrial_merchant(bigint,text,text) from public, anon, authenticated;
revoke all on function public.start_industrial_merchant_transport(bigint,bigint,text,bigint,text) from public, anon, authenticated;

grant execute on function public.industrial_truck_capacity(integer) to service_role;
grant execute on function public.industrial_trip_duration_seconds(integer) to service_role;
grant execute on function public.industrial_warehouse_capacity(integer) to service_role;
grant execute on function public.industrial_merchant_upgrade_cost(text,integer) to service_role;
grant execute on function public.ensure_and_refresh_industrial_merchant(bigint) to service_role;
grant execute on function public.get_or_create_and_refresh_industrial_merchant(bigint) to service_role;
grant execute on function public.upgrade_industrial_merchant(bigint,text,text) to service_role;
grant execute on function public.start_industrial_merchant_transport(bigint,bigint,text,bigint,text) to service_role;
