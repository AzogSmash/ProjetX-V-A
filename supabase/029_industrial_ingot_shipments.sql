-- Phase 4 : escrow explicite des lingots et transport Marchand vers Banquier.
-- Ordre global : advisory users (IDs croissants) -> shipment -> merchant -> transport -> inventory.

alter table public.industrial_transports
  drop constraint industrial_transports_transport_type_check,
  drop constraint industrial_transports_resource_type_check;
alter table public.industrial_transports
  add constraint industrial_transports_transport_type_check
    check (transport_type in ('ore_to_blacksmith', 'ingot_to_banker')),
  add constraint industrial_transports_resource_type_check
    check ((transport_type = 'ore_to_blacksmith' and resource_type = 'iron_ore')
        or (transport_type = 'ingot_to_banker' and resource_type = 'iron_ingot'));

create table public.industrial_ingot_shipments (
  id bigserial primary key,
  blacksmith_company_id bigint not null references public.industrial_companies(id) on delete restrict,
  blacksmith_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  merchant_company_id bigint not null references public.industrial_companies(id) on delete restrict,
  merchant_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  banker_company_id bigint not null references public.industrial_companies(id) on delete restrict,
  banker_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  resource_type text not null default 'iron_ingot' check (resource_type = 'iron_ingot'),
  quantity bigint not null check (quantity between 1 and 1000000),
  status text not null default 'pending' check (status in ('pending', 'accepted', 'cancelled')),
  transport_id bigint unique references public.industrial_transports(id) on delete restrict,
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  accept_request_id text unique check (accept_request_id is null or char_length(accept_request_id) between 1 and 80),
  cancel_request_id text unique check (cancel_request_id is null or char_length(cancel_request_id) between 1 and 80),
  created_at timestamptz not null default now(),
  accepted_at timestamptz,
  cancelled_at timestamptz,
  check ((status = 'pending' and accepted_at is null and cancelled_at is null and transport_id is null)
      or (status = 'accepted' and accepted_at is not null and cancelled_at is null and transport_id is not null)
      or (status = 'cancelled' and accepted_at is null and cancelled_at is not null and transport_id is null))
);

create index industrial_ingot_shipments_merchant_status_idx
  on public.industrial_ingot_shipments(merchant_discord_user_id, status, created_at);
create index industrial_ingot_shipments_blacksmith_status_idx
  on public.industrial_ingot_shipments(blacksmith_discord_user_id, status, created_at);
alter table public.industrial_ingot_shipments enable row level security;

create or replace function public.create_industrial_ingot_shipment(
  p_blacksmith_discord_user_id bigint, p_merchant_discord_user_id bigint,
  p_banker_discord_user_id bigint, p_quantity bigint, p_request_id text
)
returns table (
  result_status text, current_job text, available_amount bigint, shipment_id bigint,
  blacksmith_company_id bigint, blacksmith_discord_user_id bigint,
  merchant_company_id bigint, merchant_discord_user_id bigint,
  banker_company_id bigint, banker_discord_user_id bigint, resource_type text,
  quantity bigint, status text, created_at timestamptz, accepted_at timestamptz,
  cancelled_at timestamptz
)
language plpgsql security invoker set search_path = ''
as $$
declare s public.industrial_ingot_shipments%rowtype; blacksmith_id bigint;
  merchant_id bigint; banker_id bigint; available bigint; user_job text; lock_id bigint;
begin
  if p_quantity not between 1 and 1000000 or p_request_id is null
     or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid ingot shipment' using errcode = '22023';
  end if;
  for lock_id in select distinct v from unnest(array[p_blacksmith_discord_user_id,
    p_merchant_discord_user_id, p_banker_discord_user_id]) v order by v
  loop perform pg_catalog.pg_advisory_xact_lock(lock_id); end loop;
  select u.primary_job into user_job from public.industrial_users u
    where u.discord_user_id = p_blacksmith_discord_user_id;
  if user_job is distinct from 'blacksmith' then
    return query select 'not_blacksmith', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::bigint, null::text, null::bigint, null::text, null::timestamptz,
      null::timestamptz, null::timestamptz; return;
  end if;
  select x.* into s from public.industrial_ingot_shipments x where x.request_id = p_request_id;
  if found then
    if s.blacksmith_discord_user_id <> p_blacksmith_discord_user_id
       or s.merchant_discord_user_id <> p_merchant_discord_user_id
       or s.banker_discord_user_id <> p_banker_discord_user_id or s.quantity <> p_quantity then
      raise exception 'request id reused with different shipment parameters' using errcode = '23505';
    end if;
    return query select 'duplicate', user_job, null::bigint, s.id, s.blacksmith_company_id,
      s.blacksmith_discord_user_id, s.merchant_company_id, s.merchant_discord_user_id,
      s.banker_company_id, s.banker_discord_user_id, s.resource_type, s.quantity,
      s.status, s.created_at, s.accepted_at, s.cancelled_at; return;
  end if;
  select c.id into blacksmith_id from public.industrial_companies c
    where c.owner_discord_user_id = p_blacksmith_discord_user_id and c.is_first_company
      and c.job_type = 'blacksmith';
  if blacksmith_id is null then
    return query select 'no_blacksmith_company', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::bigint, null::text, null::bigint, null::text, null::timestamptz,
      null::timestamptz, null::timestamptz; return;
  end if;
  select c.id into merchant_id from public.industrial_companies c join public.industrial_users u
    on u.discord_user_id = c.owner_discord_user_id
    where c.owner_discord_user_id = p_merchant_discord_user_id and c.is_first_company
      and c.job_type = 'merchant' and u.primary_job = 'merchant';
  if merchant_id is null then
    return query select 'invalid_merchant', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::bigint, null::text, null::bigint, null::text, null::timestamptz,
      null::timestamptz, null::timestamptz; return;
  end if;
  select c.id into banker_id from public.industrial_companies c join public.industrial_users u
    on u.discord_user_id = c.owner_discord_user_id
    where c.owner_discord_user_id = p_banker_discord_user_id and c.is_first_company
      and c.job_type = 'banker' and u.primary_job = 'banker';
  if banker_id is null then
    return query select 'invalid_banker', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::bigint, null::text, null::bigint, null::text, null::timestamptz,
      null::timestamptz, null::timestamptz; return;
  end if;
  select i.quantity into available from public.industrial_inventory i
    where i.owner_discord_user_id = p_blacksmith_discord_user_id
      and i.resource_type = 'iron_ingot' for update;
  available := coalesce(available, 0);
  if available < p_quantity then
    return query select 'insufficient_inventory', user_job, available, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::bigint, null::text, null::bigint, null::text, null::timestamptz,
      null::timestamptz, null::timestamptz; return;
  end if;
  update public.industrial_inventory set quantity = quantity - p_quantity
    where owner_discord_user_id = p_blacksmith_discord_user_id and resource_type = 'iron_ingot';
  insert into public.industrial_ingot_shipments(
    blacksmith_company_id, blacksmith_discord_user_id, merchant_company_id,
    merchant_discord_user_id, banker_company_id, banker_discord_user_id, quantity, request_id
  ) values (blacksmith_id, p_blacksmith_discord_user_id, merchant_id,
    p_merchant_discord_user_id, banker_id, p_banker_discord_user_id, p_quantity, p_request_id)
    returning * into s;
  return query select 'ok', user_job, available - p_quantity, s.id, s.blacksmith_company_id,
    s.blacksmith_discord_user_id, s.merchant_company_id, s.merchant_discord_user_id,
    s.banker_company_id, s.banker_discord_user_id, s.resource_type, s.quantity,
    s.status, s.created_at, s.accepted_at, s.cancelled_at;
end;
$$;

create or replace function public.cancel_industrial_ingot_shipment(
  p_blacksmith_discord_user_id bigint, p_shipment_id bigint, p_request_id text
)
returns table (
  result_status text, current_job text, shipment_id bigint,
  blacksmith_company_id bigint, blacksmith_discord_user_id bigint,
  merchant_company_id bigint, merchant_discord_user_id bigint,
  banker_company_id bigint, banker_discord_user_id bigint, resource_type text,
  quantity bigint, status text, created_at timestamptz, accepted_at timestamptz,
  cancelled_at timestamptz
)
language plpgsql security invoker set search_path = ''
as $$
declare s public.industrial_ingot_shipments%rowtype; user_job text; current_time timestamptz;
begin
  if p_shipment_id < 1 or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid cancellation' using errcode = '22023'; end if;
  perform pg_catalog.pg_advisory_xact_lock(p_blacksmith_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u
    where u.discord_user_id = p_blacksmith_discord_user_id;
  select x.* into s from public.industrial_ingot_shipments x where x.id = p_shipment_id for update;
  if not found then
    return query select 'not_found', user_job, null::bigint, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::text, null::bigint,
      null::text, null::timestamptz, null::timestamptz, null::timestamptz; return; end if;
  if s.blacksmith_discord_user_id <> p_blacksmith_discord_user_id then
    return query select 'not_owner', user_job, null::bigint, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::text, null::bigint,
      null::text, null::timestamptz, null::timestamptz, null::timestamptz; return; end if;
  if s.cancel_request_id = p_request_id and s.status = 'cancelled' then
    return query select 'duplicate', user_job, s.id, s.blacksmith_company_id,
      s.blacksmith_discord_user_id, s.merchant_company_id, s.merchant_discord_user_id,
      s.banker_company_id, s.banker_discord_user_id, s.resource_type, s.quantity,
      s.status, s.created_at, s.accepted_at, s.cancelled_at; return; end if;
  if s.cancel_request_id is not null and s.cancel_request_id <> p_request_id then
    raise exception 'cancellation request id mismatch' using errcode = '23505'; end if;
  if s.status = 'accepted' then
    return query select 'already_accepted', user_job, null::bigint, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::text, null::bigint,
      null::text, null::timestamptz, null::timestamptz, null::timestamptz; return; end if;
  if s.status = 'cancelled' then
    return query select 'already_cancelled', user_job, null::bigint, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::text, null::bigint,
      null::text, null::timestamptz, null::timestamptz, null::timestamptz; return; end if;
  insert into public.industrial_inventory(owner_discord_user_id, resource_type, quantity)
    values (p_blacksmith_discord_user_id, 'iron_ingot', s.quantity)
    on conflict (owner_discord_user_id, resource_type) do update
      set quantity = industrial_inventory.quantity + excluded.quantity;
  current_time := clock_timestamp();
  update public.industrial_ingot_shipments set status = 'cancelled',
    cancelled_at = current_time, cancel_request_id = p_request_id where id = s.id returning * into s;
  return query select 'ok', user_job, s.id, s.blacksmith_company_id,
    s.blacksmith_discord_user_id, s.merchant_company_id, s.merchant_discord_user_id,
    s.banker_company_id, s.banker_discord_user_id, s.resource_type, s.quantity,
    s.status, s.created_at, s.accepted_at, s.cancelled_at;
end;
$$;

create or replace function public.accept_industrial_ingot_shipment(
  p_merchant_discord_user_id bigint, p_shipment_id bigint, p_request_id text
)
returns table (
  result_status text, current_job text, available_amount bigint, shipment_id bigint,
  blacksmith_company_id bigint, blacksmith_discord_user_id bigint,
  merchant_company_id bigint, merchant_discord_user_id bigint,
  banker_company_id bigint, banker_discord_user_id bigint, resource_type text,
  quantity bigint, status text, created_at timestamptz, accepted_at timestamptz,
  cancelled_at timestamptz, id bigint, sender_company_id bigint,
  receiver_company_id bigint, receiver_company_name text, departure_at timestamptz,
  arrival_at timestamptz, truck_slot integer
)
language plpgsql security invoker set search_path = ''
as $$
declare s public.industrial_ingot_shipments%rowtype; m public.industrial_merchants%rowtype;
  t public.industrial_transports%rowtype; user_job text; free_slot integer;
  capacity bigint; duration_seconds integer; current_time timestamptz; banker_name text;
  lock_id bigint;
begin
  if p_shipment_id < 1 or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid acceptance' using errcode = '22023'; end if;
  select x.* into s from public.industrial_ingot_shipments x where x.id = p_shipment_id;
  if not found then
    return query select 'not_found', null::text, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  for lock_id in select distinct v from unnest(array[s.blacksmith_discord_user_id,
    s.merchant_discord_user_id, s.banker_discord_user_id]) v order by v
  loop perform pg_catalog.pg_advisory_xact_lock(lock_id); end loop;
  select u.primary_job into user_job from public.industrial_users u
    where u.discord_user_id = p_merchant_discord_user_id;
  if user_job is distinct from 'merchant' then
    return query select 'not_merchant', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  select x.* into s from public.industrial_ingot_shipments x where x.id = p_shipment_id for update;
  if s.merchant_discord_user_id <> p_merchant_discord_user_id then
    return query select 'not_designated_merchant', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  if s.accept_request_id = p_request_id and s.status = 'accepted' then
    select x.* into t from public.industrial_transports x where x.id = s.transport_id;
    select c.name into banker_name from public.industrial_companies c where c.id = s.banker_company_id;
    return query select 'duplicate', user_job, null::bigint, s.id, s.blacksmith_company_id,
      s.blacksmith_discord_user_id, s.merchant_company_id, s.merchant_discord_user_id,
      s.banker_company_id, s.banker_discord_user_id, s.resource_type, s.quantity,
      s.status, s.created_at, s.accepted_at, s.cancelled_at, t.id, t.sender_company_id,
      t.receiver_company_id, banker_name, t.departure_at, t.arrival_at, t.truck_slot; return; end if;
  if s.status = 'cancelled' then
    return query select 'already_cancelled', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  if s.status = 'accepted' then
    return query select 'already_accepted', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  select x.* into m from public.industrial_merchants x
    where x.owner_discord_user_id = p_merchant_discord_user_id for update;
  if not found or m.company_id <> s.merchant_company_id then
    return query select 'no_merchant_company', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  capacity := public.industrial_truck_capacity(m.truck_capacity_level);
  if s.quantity > capacity then
    return query select 'capacity_exceeded', user_job, capacity, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  select slot into free_slot from pg_catalog.generate_series(1, m.truck_count) slots(slot)
    where not exists (select 1 from public.industrial_transports x
      where x.merchant_discord_user_id = p_merchant_discord_user_id
        and x.truck_slot = slot and x.status = 'in_transit') order by slot limit 1;
  if free_slot is null then
    return query select 'no_truck_available', user_job, null::bigint, null::bigint,
      null::bigint, null::bigint, null::bigint, null::bigint, null::bigint, null::bigint,
      null::text, null::bigint, null::text, null::timestamptz, null::timestamptz,
      null::timestamptz, null::bigint, null::bigint, null::bigint, null::text,
      null::timestamptz, null::timestamptz, null::integer; return; end if;
  duration_seconds := public.industrial_trip_duration_seconds(m.truck_speed_level);
  current_time := clock_timestamp();
  insert into public.industrial_transports(sender_company_id, receiver_company_id,
    merchant_discord_user_id, transport_type, resource_type, quantity, departure_at,
    arrival_at, original_duration_seconds, current_duration_seconds, truck_slot, request_id)
  values (s.blacksmith_company_id, s.banker_company_id, p_merchant_discord_user_id,
    'ingot_to_banker', 'iron_ingot', s.quantity, current_time,
    current_time + pg_catalog.make_interval(secs => duration_seconds),
    duration_seconds, duration_seconds, free_slot, p_request_id) returning * into t;
  update public.industrial_ingot_shipments set status = 'accepted', accepted_at = current_time,
    accept_request_id = p_request_id, transport_id = t.id where id = s.id returning * into s;
  select c.name into banker_name from public.industrial_companies c where c.id = s.banker_company_id;
  return query select 'ok', user_job, null::bigint, s.id, s.blacksmith_company_id,
    s.blacksmith_discord_user_id, s.merchant_company_id, s.merchant_discord_user_id,
    s.banker_company_id, s.banker_discord_user_id, s.resource_type, s.quantity,
    s.status, s.created_at, s.accepted_at, s.cancelled_at, t.id, t.sender_company_id,
    t.receiver_company_id, banker_name, t.departure_at, t.arrival_at, t.truck_slot;
end;
$$;

revoke all on table public.industrial_ingot_shipments from public, anon, authenticated;
revoke all on sequence public.industrial_ingot_shipments_id_seq from public, anon, authenticated;
grant select, insert, update on table public.industrial_ingot_shipments to service_role;
grant usage, select on sequence public.industrial_ingot_shipments_id_seq to service_role;
revoke all on function public.create_industrial_ingot_shipment(bigint,bigint,bigint,bigint,text) from public, anon, authenticated;
revoke all on function public.cancel_industrial_ingot_shipment(bigint,bigint,text) from public, anon, authenticated;
revoke all on function public.accept_industrial_ingot_shipment(bigint,bigint,text) from public, anon, authenticated;
grant execute on function public.create_industrial_ingot_shipment(bigint,bigint,bigint,bigint,text) to service_role;
grant execute on function public.cancel_industrial_ingot_shipment(bigint,bigint,text) to service_role;
grant execute on function public.accept_industrial_ingot_shipment(bigint,bigint,text) to service_role;
