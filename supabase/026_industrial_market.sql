-- Phase 1 : carnet d'ordres industriel pour le minerai de fer.
-- Ordre global des verrous : ressource marché (advisory) -> ordre -> utilisateur -> inventaire.

create table public.industrial_market_orders (
  id bigserial primary key,
  owner_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  company_id bigint not null references public.industrial_companies(id) on delete restrict,
  side text not null check (side in ('buy', 'sell')),
  resource_type text not null check (resource_type in ('iron_ore')),
  original_quantity bigint not null check (original_quantity between 1 and 1000000),
  remaining_quantity bigint not null check (remaining_quantity between 0 and original_quantity),
  unit_price bigint not null check (unit_price between 1 and 1000000),
  escrow_quantity bigint not null default 0 check (escrow_quantity >= 0),
  escrow_credits bigint not null default 0 check (escrow_credits >= 0),
  status text not null default 'open' check (status in ('open', 'filled', 'cancelled')),
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  closed_at timestamptz,
  check (
    (side = 'sell' and escrow_credits = 0 and escrow_quantity = remaining_quantity)
    or (side = 'buy' and escrow_quantity = 0 and escrow_credits = remaining_quantity * unit_price)
  ),
  check ((status = 'open' and remaining_quantity > 0 and closed_at is null)
      or (status in ('filled', 'cancelled') and remaining_quantity = 0 and closed_at is not null))
);

create table public.industrial_market_trades (
  id bigserial primary key,
  resource_type text not null check (resource_type in ('iron_ore')),
  quantity bigint not null check (quantity between 1 and 1000000),
  unit_price bigint not null check (unit_price between 1 and 1000000),
  total_price bigint generated always as (quantity * unit_price) stored,
  seller_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  buyer_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  sell_order_id bigint not null references public.industrial_market_orders(id) on delete restrict,
  buy_order_id bigint not null references public.industrial_market_orders(id) on delete restrict,
  created_at timestamptz not null default now(),
  check (seller_discord_user_id <> buyer_discord_user_id)
);

create index industrial_market_orders_book_idx
  on public.industrial_market_orders(resource_type, side, status, unit_price, created_at, id);
create index industrial_market_orders_owner_idx
  on public.industrial_market_orders(owner_discord_user_id, status, created_at);
create index industrial_market_trades_resource_time_idx
  on public.industrial_market_trades(resource_type, created_at desc);

create trigger industrial_market_orders_set_updated_at
before update on public.industrial_market_orders
for each row execute function public.set_industrial_updated_at();

alter table public.industrial_market_orders enable row level security;
alter table public.industrial_market_trades enable row level security;

create or replace function public.create_industrial_market_order(
  p_owner_discord_user_id bigint, p_side text, p_resource_type text,
  p_quantity bigint, p_unit_price bigint, p_request_id text
)
returns table (
  result_status text, available_amount bigint, id bigint,
  owner_discord_user_id bigint, side text, resource_type text,
  original_quantity bigint, remaining_quantity bigint, unit_price bigint,
  status text, created_at timestamptz, filled_quantity bigint
)
language plpgsql security invoker set search_path = ''
as $$
declare
  actor_job text; actor_company bigint; available bigint; open_count integer;
  incoming public.industrial_market_orders%rowtype;
  counter public.industrial_market_orders%rowtype;
  buy_order public.industrial_market_orders%rowtype;
  sell_order public.industrial_market_orders%rowtype;
  fill_quantity bigint; trade_price bigint; trade_total bigint; buyer_refund bigint;
begin
  if p_side not in ('buy', 'sell') or p_resource_type <> 'iron_ore'
     or p_quantity not between 1 and 1000000 or p_unit_price not between 1 and 1000000
     or p_request_id is null or char_length(p_request_id) not between 1 and 80 then
    raise exception 'invalid industrial market order' using errcode = '22023';
  end if;

  -- Une clé de namespace fixe et hashtext(resource) donnent une clé stable par ressource.
  perform pg_catalog.pg_advisory_xact_lock(1229801291, pg_catalog.hashtext(p_resource_type));

  select o.* into incoming from public.industrial_market_orders o
  where o.request_id = p_request_id;
  if found then
    if incoming.owner_discord_user_id <> p_owner_discord_user_id
       or incoming.side <> p_side or incoming.resource_type <> p_resource_type
       or incoming.original_quantity <> p_quantity or incoming.unit_price <> p_unit_price then
      raise exception 'request id reused with different market parameters' using errcode = '23505';
    end if;
    return query select 'duplicate'::text, null::bigint, incoming.id,
      incoming.owner_discord_user_id, incoming.side, incoming.resource_type,
      incoming.original_quantity, incoming.remaining_quantity, incoming.unit_price,
      incoming.status, incoming.created_at,
      incoming.original_quantity - incoming.remaining_quantity;
    return;
  end if;

  select u.primary_job, c.id into actor_job, actor_company
  from public.industrial_users u
  join public.industrial_companies c on c.owner_discord_user_id = u.discord_user_id
    and c.is_first_company and c.job_type = u.primary_job
  where u.discord_user_id = p_owner_discord_user_id;

  if p_side = 'sell' and actor_job is distinct from 'miner' then
    return query select 'not_miner', null::bigint, null::bigint, null::bigint,
      null::text, null::text, null::bigint, null::bigint, null::bigint,
      null::text, null::timestamptz, null::bigint; return;
  elsif p_side = 'buy' and actor_job is distinct from 'merchant' then
    return query select 'not_merchant', null::bigint, null::bigint, null::bigint,
      null::text, null::text, null::bigint, null::bigint, null::bigint,
      null::text, null::timestamptz, null::bigint; return;
  end if;

  select count(*) into open_count from public.industrial_market_orders o
  where o.owner_discord_user_id = p_owner_discord_user_id and o.status = 'open';
  if open_count >= 20 then
    return query select 'order_limit', null::bigint, null::bigint, null::bigint,
      null::text, null::text, null::bigint, null::bigint, null::bigint,
      null::text, null::timestamptz, null::bigint; return;
  end if;

  if p_side = 'sell' then
    select i.quantity into available from public.industrial_inventory i
    where i.owner_discord_user_id = p_owner_discord_user_id and i.resource_type = p_resource_type for update;
    available := coalesce(available, 0);
    if available < p_quantity then
      return query select 'insufficient_inventory', available, null::bigint, null::bigint,
        null::text, null::text, null::bigint, null::bigint, null::bigint,
        null::text, null::timestamptz, null::bigint; return;
    end if;
    update public.industrial_inventory set quantity = quantity - p_quantity
    where owner_discord_user_id = p_owner_discord_user_id and resource_type = p_resource_type;
  else
    select u.credits into available from public.industrial_users u
    where u.discord_user_id = p_owner_discord_user_id for update;
    if available < p_quantity * p_unit_price then
      return query select 'insufficient_funds', available, null::bigint, null::bigint,
        null::text, null::text, null::bigint, null::bigint, null::bigint,
        null::text, null::timestamptz, null::bigint; return;
    end if;
    update public.industrial_users set credits = credits - p_quantity * p_unit_price
    where discord_user_id = p_owner_discord_user_id;
  end if;

  insert into public.industrial_market_orders (
    owner_discord_user_id, company_id, side, resource_type, original_quantity,
    remaining_quantity, unit_price, escrow_quantity, escrow_credits, request_id
  ) values (
    p_owner_discord_user_id, actor_company, p_side, p_resource_type, p_quantity,
    p_quantity, p_unit_price,
    case when p_side = 'sell' then p_quantity else 0 end,
    case when p_side = 'buy' then p_quantity * p_unit_price else 0 end,
    p_request_id
  ) returning * into incoming;

  loop
    if incoming.remaining_quantity = 0 then exit; end if;
    if p_side = 'buy' then
      select o.* into counter from public.industrial_market_orders o
      where o.resource_type = p_resource_type and o.side = 'sell' and o.status = 'open'
        and o.unit_price <= p_unit_price and o.owner_discord_user_id <> p_owner_discord_user_id
      order by o.unit_price, o.created_at, o.id limit 1 for update;
    else
      select o.* into counter from public.industrial_market_orders o
      where o.resource_type = p_resource_type and o.side = 'buy' and o.status = 'open'
        and o.unit_price >= p_unit_price and o.owner_discord_user_id <> p_owner_discord_user_id
      order by o.unit_price desc, o.created_at, o.id limit 1 for update;
    end if;
    if not found then exit; end if;

    if p_side = 'buy' then buy_order := incoming; sell_order := counter;
    else buy_order := counter; sell_order := incoming; end if;
    fill_quantity := least(buy_order.remaining_quantity, sell_order.remaining_quantity);
    trade_price := counter.unit_price; -- prix de l'ordre maker, puis priorité prix/ancienneté
    trade_total := fill_quantity * trade_price;
    buyer_refund := fill_quantity * (buy_order.unit_price - trade_price);

    update public.industrial_users set credits = credits + trade_total
    where discord_user_id = sell_order.owner_discord_user_id;
    if buyer_refund > 0 then
      update public.industrial_users set credits = credits + buyer_refund
      where discord_user_id = buy_order.owner_discord_user_id;
    end if;
    insert into public.industrial_inventory(owner_discord_user_id, resource_type, quantity)
    values (buy_order.owner_discord_user_id, p_resource_type, fill_quantity)
    on conflict (owner_discord_user_id, resource_type) do update
      set quantity = industrial_inventory.quantity + excluded.quantity;

    update public.industrial_market_orders set
      remaining_quantity = remaining_quantity - fill_quantity,
      escrow_quantity = escrow_quantity - case when side = 'sell' then fill_quantity else 0 end,
      escrow_credits = escrow_credits - case when side = 'buy' then fill_quantity * unit_price else 0 end,
      status = case when remaining_quantity = fill_quantity then 'filled' else 'open' end,
      closed_at = case when remaining_quantity = fill_quantity then clock_timestamp() else null end
    where id in (buy_order.id, sell_order.id);

    insert into public.industrial_market_trades(
      resource_type, quantity, unit_price, seller_discord_user_id,
      buyer_discord_user_id, sell_order_id, buy_order_id
    ) values (p_resource_type, fill_quantity, trade_price, sell_order.owner_discord_user_id,
      buy_order.owner_discord_user_id, sell_order.id, buy_order.id);

    select o.* into incoming from public.industrial_market_orders o where o.id = incoming.id;
  end loop;

  return query select 'ok'::text, null::bigint, incoming.id,
    incoming.owner_discord_user_id, incoming.side, incoming.resource_type,
    incoming.original_quantity, incoming.remaining_quantity, incoming.unit_price,
    incoming.status, incoming.created_at,
    incoming.original_quantity - incoming.remaining_quantity;
end;
$$;

create or replace function public.cancel_industrial_market_order(
  p_owner_discord_user_id bigint, p_order_id bigint
)
returns table (
  result_status text, id bigint, owner_discord_user_id bigint, side text,
  resource_type text, original_quantity bigint, remaining_quantity bigint,
  unit_price bigint, status text, created_at timestamptz
)
language plpgsql security invoker set search_path = ''
as $$
declare order_row public.industrial_market_orders%rowtype; resource_name text;
begin
  select o.resource_type into resource_name from public.industrial_market_orders o
  where o.id = p_order_id and o.owner_discord_user_id = p_owner_discord_user_id;
  if not found then
    return query select 'not_found', null::bigint, null::bigint, null::text,
      null::text, null::bigint, null::bigint, null::bigint, null::text, null::timestamptz; return;
  end if;
  perform pg_catalog.pg_advisory_xact_lock(1229801291, pg_catalog.hashtext(resource_name));
  select o.* into order_row from public.industrial_market_orders o
  where o.id = p_order_id and o.owner_discord_user_id = p_owner_discord_user_id for update;
  if order_row.status <> 'open' then
    return query select 'already_closed', null::bigint, null::bigint, null::text,
      null::text, null::bigint, null::bigint, null::bigint, null::text, null::timestamptz; return;
  end if;
  if order_row.side = 'sell' then
    insert into public.industrial_inventory(owner_discord_user_id, resource_type, quantity)
    values (p_owner_discord_user_id, order_row.resource_type, order_row.escrow_quantity)
    on conflict (owner_discord_user_id, resource_type) do update
      set quantity = industrial_inventory.quantity + excluded.quantity;
  else
    update public.industrial_users set credits = credits + order_row.escrow_credits
    where discord_user_id = p_owner_discord_user_id;
  end if;
  update public.industrial_market_orders set remaining_quantity = 0,
    escrow_quantity = 0, escrow_credits = 0, status = 'cancelled', closed_at = clock_timestamp()
  where industrial_market_orders.id = p_order_id returning * into order_row;
  return query select 'ok', order_row.id, order_row.owner_discord_user_id,
    order_row.side, order_row.resource_type, order_row.original_quantity,
    order_row.remaining_quantity, order_row.unit_price, order_row.status, order_row.created_at;
end;
$$;

create or replace function public.get_industrial_market_stats(p_resource_type text)
returns table (average_price_24h numeric, low_price_24h bigint, high_price_24h bigint, volume_24h bigint)
language sql stable security invoker set search_path = ''
as $$
  select round(sum(t.total_price)::numeric / nullif(sum(t.quantity), 0), 2),
    min(t.unit_price), max(t.unit_price), coalesce(sum(t.quantity), 0)::bigint
  from public.industrial_market_trades t
  where t.resource_type = p_resource_type and t.created_at >= now() - interval '24 hours';
$$;

revoke all on table public.industrial_market_orders, public.industrial_market_trades from public, anon, authenticated;
revoke all on sequence public.industrial_market_orders_id_seq, public.industrial_market_trades_id_seq from public, anon, authenticated;
grant select, insert, update on table public.industrial_market_orders to service_role;
grant select, insert on table public.industrial_market_trades to service_role;
grant usage, select on sequence public.industrial_market_orders_id_seq, public.industrial_market_trades_id_seq to service_role;
revoke all on function public.create_industrial_market_order(bigint,text,text,bigint,bigint,text) from public, anon, authenticated;
revoke all on function public.cancel_industrial_market_order(bigint,bigint) from public, anon, authenticated;
revoke all on function public.get_industrial_market_stats(text) from public, anon, authenticated;
grant execute on function public.create_industrial_market_order(bigint,text,text,bigint,bigint,text) to service_role;
grant execute on function public.cancel_industrial_market_order(bigint,bigint) to service_role;
grant execute on function public.get_industrial_market_stats(text) to service_role;
