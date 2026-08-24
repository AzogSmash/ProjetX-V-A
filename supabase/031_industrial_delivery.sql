-- Phase 6 : missions de livraison et escrow CR financé par le Marchand.
-- Conservation : commission_max = commission_paid + merchant_refund, escrow_remaining finit à zéro.

create table public.industrial_delivery_profiles (
  discord_user_id bigint primary key references public.industrial_users(discord_user_id) on delete restrict,
  delivery_level integer not null default 1 check (delivery_level between 1 and 100),
  delivery_xp bigint not null default 0 check (delivery_xp >= 0),
  completed_deliveries bigint not null default 0 check (completed_deliveries >= 0),
  delivery_cooldown_until timestamptz, created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create table public.industrial_delivery_missions (
  id bigserial primary key,
  transport_id bigint not null unique references public.industrial_transports(id) on delete restrict,
  merchant_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  resource_type text not null check (resource_type in ('iron_ore','iron_ingot')),
  quantity bigint not null check (quantity between 1 and 1000000),
  status text not null default 'open' check (status in ('open','accepted','refunded')),
  commission_max bigint not null check (commission_max between 20 and 5000),
  escrow_remaining bigint not null check (escrow_remaining >= 0 and escrow_remaining <= commission_max),
  courier_discord_user_id bigint references public.industrial_users(discord_user_id) on delete restrict,
  commission_paid bigint check (commission_paid is null or commission_paid >= 0),
  merchant_refund bigint check (merchant_refund is null or merchant_refund >= 0),
  saved_seconds integer check (saved_seconds is null or saved_seconds >= 0),
  xp_awarded integer check (xp_awarded is null or xp_awarded >= 0),
  accept_request_id text unique check (accept_request_id is null or char_length(accept_request_id) between 1 and 80),
  created_at timestamptz not null default now(), accepted_at timestamptz, refunded_at timestamptz,
  check ((status='open' and escrow_remaining=commission_max and courier_discord_user_id is null)
    or (status='accepted' and escrow_remaining=0 and courier_discord_user_id is not null
      and commission_paid + merchant_refund = commission_max and accepted_at is not null)
    or (status='refunded' and escrow_remaining=0 and courier_discord_user_id is null
      and commission_paid=0 and merchant_refund=commission_max and refunded_at is not null))
);
create index industrial_delivery_missions_open_idx on public.industrial_delivery_missions(status,created_at)
  where status='open';
alter table public.industrial_delivery_profiles enable row level security;
alter table public.industrial_delivery_missions enable row level security;

create or replace function public.industrial_delivery_commission_max(p_quantity bigint)
returns bigint language sql immutable strict security invoker set search_path=''
as $$ select greatest(20::bigint,least(5000::bigint,p_quantity*2)); $$;
create or replace function public.industrial_delivery_reduction_seconds(p_level integer)
returns integer language sql immutable strict security invoker set search_path=''
as $$ select least(1800,greatest(1,p_level)*180); $$;
create or replace function public.industrial_delivery_cooldown_seconds(p_level integer)
returns integer language sql immutable strict security invoker set search_path=''
as $$ select greatest(300,1800-(greatest(1,p_level)-1)*60); $$;
create or replace function public.industrial_delivery_level(p_xp bigint)
returns integer language sql immutable strict security invoker set search_path=''
as $$ select least(100,1+floor(sqrt(greatest(0,p_xp)::numeric/100))::integer); $$;

create or replace function public.refund_industrial_delivery_on_arrival()
returns trigger language plpgsql set search_path=''
as $$
declare m public.industrial_delivery_missions%rowtype;
begin
  if old.status='in_transit' and new.status='delivered' then
    select x.* into m from public.industrial_delivery_missions x where x.transport_id=new.id for update;
    if found and m.status='open' then
      update public.industrial_users set credits=credits+m.commission_max
        where discord_user_id=m.merchant_discord_user_id;
      update public.industrial_delivery_missions set status='refunded',escrow_remaining=0,
        commission_paid=0,merchant_refund=commission_max,refunded_at=clock_timestamp()
        where id=m.id and status='open';
    end if;
  end if;
  return new;
end;
$$;
create trigger industrial_transport_refund_delivery after update of status on public.industrial_transports
  for each row execute function public.refund_industrial_delivery_on_arrival();

create or replace function public.start_industrial_merchant_transport_with_delivery(
  p_merchant_discord_user_id bigint,p_receiver_discord_user_id bigint,
  p_resource_type text,p_quantity bigint,p_request_id text
)
returns table(result_status text,current_job text,available_amount bigint,id bigint,
  sender_company_id bigint,receiver_company_id bigint,receiver_company_name text,
  merchant_discord_user_id bigint,resource_type text,quantity bigint,
  departure_at timestamptz,arrival_at timestamptz,status text,truck_slot integer)
language plpgsql security invoker set search_path=''
as $$
declare tr record; existing public.industrial_transports%rowtype; fee bigint; balance bigint;
begin
  perform pg_catalog.pg_advisory_xact_lock(p_merchant_discord_user_id);
  select t.* into existing from public.industrial_transports t where t.request_id=p_request_id;
  if found then
    return query select * from public.start_industrial_merchant_transport(
      p_merchant_discord_user_id,p_receiver_discord_user_id,p_resource_type,p_quantity,p_request_id); return;
  end if;
  fee:=public.industrial_delivery_commission_max(p_quantity);
  select u.credits into balance from public.industrial_users u
    where u.discord_user_id=p_merchant_discord_user_id for update;
  if coalesce(balance,0)<fee then
    return query select 'insufficient_commission_funds',u.primary_job,balance,null::bigint,
      null::bigint,null::bigint,null::text,null::bigint,null::text,null::bigint,
      null::timestamptz,null::timestamptz,null::text,null::integer
      from public.industrial_users u where u.discord_user_id=p_merchant_discord_user_id; return;
  end if;
  update public.industrial_users set credits=credits-fee where discord_user_id=p_merchant_discord_user_id;
  select * into tr from public.start_industrial_merchant_transport(
    p_merchant_discord_user_id,p_receiver_discord_user_id,p_resource_type,p_quantity,p_request_id);
  if tr.result_status not in ('ok','duplicate') then
    update public.industrial_users set credits=credits+fee where discord_user_id=p_merchant_discord_user_id;
  else
    insert into public.industrial_delivery_missions(transport_id,merchant_discord_user_id,
      resource_type,quantity,commission_max,escrow_remaining)
      values(tr.id,p_merchant_discord_user_id,p_resource_type,p_quantity,fee,fee)
      on conflict(transport_id) do nothing;
  end if;
  return query select tr.result_status,tr.current_job,tr.available_amount,tr.id,
    tr.sender_company_id,tr.receiver_company_id,tr.receiver_company_name,
    tr.merchant_discord_user_id,tr.resource_type,tr.quantity,tr.departure_at,
    tr.arrival_at,tr.status,tr.truck_slot;
end;
$$;

create or replace function public.accept_industrial_ingot_shipment_with_delivery(
  p_merchant_discord_user_id bigint,p_shipment_id bigint,p_request_id text
)
returns table(result_status text,current_job text,available_amount bigint,shipment_id bigint,
  blacksmith_company_id bigint,blacksmith_discord_user_id bigint,merchant_company_id bigint,
  merchant_discord_user_id bigint,banker_company_id bigint,banker_discord_user_id bigint,
  resource_type text,quantity bigint,status text,created_at timestamptz,accepted_at timestamptz,
  cancelled_at timestamptz,id bigint,sender_company_id bigint,receiver_company_id bigint,
  receiver_company_name text,departure_at timestamptz,arrival_at timestamptz,truck_slot integer)
language plpgsql security invoker set search_path=''
as $$
declare tr record; existing public.industrial_transports%rowtype; shipment public.industrial_ingot_shipments%rowtype;
  fee bigint; balance bigint;lock_id bigint;
begin
  select s.* into shipment from public.industrial_ingot_shipments s where s.id=p_shipment_id;
  if shipment.id is null then
    return query select * from public.accept_industrial_ingot_shipment(
      p_merchant_discord_user_id,p_shipment_id,p_request_id); return; end if;
  if shipment.transport_id is not null then
    return query select * from public.accept_industrial_ingot_shipment(
      p_merchant_discord_user_id,p_shipment_id,p_request_id); return; end if;
  for lock_id in select distinct v from unnest(array[shipment.blacksmith_discord_user_id,
    shipment.merchant_discord_user_id,shipment.banker_discord_user_id])v order by v
  loop perform pg_catalog.pg_advisory_xact_lock(lock_id);end loop;
  fee:=public.industrial_delivery_commission_max(coalesce(shipment.quantity,0));
  select u.credits into balance from public.industrial_users u
    where u.discord_user_id=p_merchant_discord_user_id for update;
  if coalesce(balance,0)<fee then
    return query select 'insufficient_commission_funds',u.primary_job,balance,null::bigint,
      null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,
      null::text,null::bigint,null::text,null::timestamptz,null::timestamptz,null::timestamptz,
      null::bigint,null::bigint,null::bigint,null::text,null::timestamptz,null::timestamptz,null::integer
      from public.industrial_users u where u.discord_user_id=p_merchant_discord_user_id; return;
  end if;
  update public.industrial_users set credits=credits-fee where discord_user_id=p_merchant_discord_user_id;
  select * into tr from public.accept_industrial_ingot_shipment(
    p_merchant_discord_user_id,p_shipment_id,p_request_id);
  if tr.result_status not in ('ok','duplicate') then
    update public.industrial_users set credits=credits+fee where discord_user_id=p_merchant_discord_user_id;
  else
    insert into public.industrial_delivery_missions(transport_id,merchant_discord_user_id,
      resource_type,quantity,commission_max,escrow_remaining)
      values(tr.id,p_merchant_discord_user_id,'iron_ingot',tr.quantity,fee,fee)
      on conflict(transport_id) do nothing;
  end if;
  return query select tr.result_status,tr.current_job,tr.available_amount,tr.shipment_id,
    tr.blacksmith_company_id,tr.blacksmith_discord_user_id,tr.merchant_company_id,
    tr.merchant_discord_user_id,tr.banker_company_id,tr.banker_discord_user_id,
    tr.resource_type,tr.quantity,tr.status,tr.created_at,tr.accepted_at,tr.cancelled_at,
    tr.id,tr.sender_company_id,tr.receiver_company_id,tr.receiver_company_name,
    tr.departure_at,tr.arrival_at,tr.truck_slot;
end;
$$;

create or replace function public.get_industrial_delivery_profile(p_discord_user_id bigint)
returns table(delivery_level integer,delivery_xp bigint,completed_deliveries bigint,
  delivery_cooldown_until timestamptz)
language plpgsql security invoker set search_path=''
as $$ begin
  insert into public.industrial_users(discord_user_id) values(p_discord_user_id)
    on conflict(discord_user_id) do nothing;
  insert into public.industrial_delivery_profiles(discord_user_id) values(p_discord_user_id)
    on conflict(discord_user_id) do nothing;
  return query select p.delivery_level,p.delivery_xp,p.completed_deliveries,p.delivery_cooldown_until
    from public.industrial_delivery_profiles p where p.discord_user_id=p_discord_user_id;
end; $$;

create or replace function public.accept_industrial_delivery(
  p_courier_discord_user_id bigint,p_mission_id bigint,p_request_id text
)
returns table(result_status text,mission_id bigint,saved_seconds integer,
  commission_paid bigint,merchant_refund bigint,xp_awarded integer,new_level integer,
  cooldown_until timestamptz)
language plpgsql security invoker set search_path=''
as $$
declare m public.industrial_delivery_missions%rowtype;t public.industrial_transports%rowtype;
  p public.industrial_delivery_profiles%rowtype;now_at timestamptz;max_reduction integer;
  saved integer;paid bigint;refunded bigint;xp_gain integer;level_after integer;cooldown timestamptz;
begin
  if p_mission_id<1 or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid delivery acceptance' using errcode='22023'; end if;
  perform pg_catalog.pg_advisory_xact_lock(p_courier_discord_user_id);
  select x.* into m from public.industrial_delivery_missions x where x.id=p_mission_id;
  if not found then return query select 'not_found',null::bigint,null::integer,null::bigint,
    null::bigint,null::integer,null::integer,null::timestamptz;return;end if;
  select x.* into t from public.industrial_transports x where x.id=m.transport_id for update;
  select x.* into m from public.industrial_delivery_missions x where x.id=p_mission_id for update;
  if m.accept_request_id=p_request_id and m.status='accepted' then
    select x.* into p from public.industrial_delivery_profiles x where x.discord_user_id=p_courier_discord_user_id;
    return query select 'duplicate',m.id,m.saved_seconds,m.commission_paid,m.merchant_refund,
      m.xp_awarded,p.delivery_level,p.delivery_cooldown_until;return;end if;
  if m.status='accepted' then return query select 'already_taken',null::bigint,null::integer,
    null::bigint,null::bigint,null::integer,null::integer,null::timestamptz;return;end if;
  if m.status='refunded' then return query select 'arrived',null::bigint,null::integer,
    null::bigint,null::bigint,null::integer,null::integer,null::timestamptz;return;end if;
  if m.merchant_discord_user_id=p_courier_discord_user_id then return query select 'own_transport',
    null::bigint,null::integer,null::bigint,null::bigint,null::integer,null::integer,null::timestamptz;return;end if;
  now_at:=clock_timestamp();
  if t.status<>'in_transit' or t.arrival_at<=now_at then
    update public.industrial_transports set status='delivered',completed_at=now_at
      where id=t.id and status='in_transit';
    return query select 'arrived',null::bigint,null::integer,null::bigint,null::bigint,
      null::integer,null::integer,null::timestamptz;return;end if;
  insert into public.industrial_users(discord_user_id) values(p_courier_discord_user_id)
    on conflict(discord_user_id) do nothing;
  insert into public.industrial_delivery_profiles(discord_user_id) values(p_courier_discord_user_id)
    on conflict(discord_user_id) do nothing;
  select x.* into p from public.industrial_delivery_profiles x
    where x.discord_user_id=p_courier_discord_user_id for update;
  if p.delivery_cooldown_until is not null and p.delivery_cooldown_until>now_at then
    return query select 'cooldown',null::bigint,null::integer,null::bigint,null::bigint,
      null::integer,null::integer,p.delivery_cooldown_until;return;end if;
  max_reduction:=public.industrial_delivery_reduction_seconds(p.delivery_level);
  saved:=least(max_reduction,greatest(0,floor(extract(epoch from(t.arrival_at-now_at)))::integer));
  paid:=least(m.commission_max,greatest(0,(m.commission_max*saved)/max_reduction));
  refunded:=m.commission_max-paid;xp_gain:=20+saved/60;
  level_after:=public.industrial_delivery_level(p.delivery_xp+xp_gain);
  cooldown:=now_at+pg_catalog.make_interval(secs=>public.industrial_delivery_cooldown_seconds(level_after));
  update public.industrial_transports set arrival_at=greatest(now_at,arrival_at-pg_catalog.make_interval(secs=>saved)),
    current_duration_seconds=greatest(0,current_duration_seconds-saved) where id=t.id;
  update public.industrial_users set credits=credits+paid where discord_user_id=p_courier_discord_user_id;
  update public.industrial_users set credits=credits+refunded where discord_user_id=m.merchant_discord_user_id;
  update public.industrial_delivery_profiles set delivery_xp=delivery_xp+xp_gain,
    delivery_level=level_after,completed_deliveries=completed_deliveries+1,
    delivery_cooldown_until=cooldown,updated_at=now_at where discord_user_id=p_courier_discord_user_id;
  update public.industrial_delivery_missions set status='accepted',escrow_remaining=0,
    courier_discord_user_id=p_courier_discord_user_id,commission_paid=paid,
    merchant_refund=refunded,saved_seconds=saved,xp_awarded=xp_gain,
    accept_request_id=p_request_id,accepted_at=now_at where id=m.id;
  return query select 'ok',m.id,saved,paid,refunded,xp_gain,level_after,cooldown;
end;
$$;

revoke all on table public.industrial_delivery_profiles,public.industrial_delivery_missions from public,anon,authenticated;
revoke all on sequence public.industrial_delivery_missions_id_seq from public,anon,authenticated;
grant select,insert,update on table public.industrial_delivery_profiles,public.industrial_delivery_missions to service_role;
grant usage,select on sequence public.industrial_delivery_missions_id_seq to service_role;
revoke all on function public.industrial_delivery_commission_max(bigint),public.industrial_delivery_reduction_seconds(integer),
 public.industrial_delivery_cooldown_seconds(integer),public.industrial_delivery_level(bigint),
 public.start_industrial_merchant_transport_with_delivery(bigint,bigint,text,bigint,text),
 public.accept_industrial_ingot_shipment_with_delivery(bigint,bigint,text),
 public.get_industrial_delivery_profile(bigint),public.accept_industrial_delivery(bigint,bigint,text)
 from public,anon,authenticated;
revoke all on function public.refund_industrial_delivery_on_arrival() from public,anon,authenticated;
grant execute on function public.industrial_delivery_commission_max(bigint),public.industrial_delivery_reduction_seconds(integer),
 public.industrial_delivery_cooldown_seconds(integer),public.industrial_delivery_level(bigint),
 public.start_industrial_merchant_transport_with_delivery(bigint,bigint,text,bigint,text),
 public.accept_industrial_ingot_shipment_with_delivery(bigint,bigint,text),
 public.get_industrial_delivery_profile(bigint),public.accept_industrial_delivery(bigint,bigint,text)
 to service_role;
