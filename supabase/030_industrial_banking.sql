-- Phase 5 : réception lazy des lingots et marché mondial déterministe.
-- Source monétaire explicite : seules les ventes mondiales créditent de nouveaux CR.

create table public.industrial_bankers (
  owner_discord_user_id bigint primary key references public.industrial_users(discord_user_id) on delete restrict,
  company_id bigint not null unique references public.industrial_companies(id) on delete cascade,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.industrial_world_sales (
  id bigserial primary key,
  banker_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  banker_company_id bigint not null references public.industrial_companies(id) on delete restrict,
  resource_type text not null check (resource_type = 'iron_ingot'),
  quantity bigint not null check (quantity between 1 and 1000000),
  unit_price bigint not null check (unit_price between 50 and 120),
  total_credits bigint not null check (total_credits = quantity * unit_price and total_credits > 0),
  balance_after bigint not null check (balance_after >= 0),
  request_id text not null unique check (char_length(request_id) between 1 and 80),
  created_at timestamptz not null default now()
);
create index industrial_world_sales_resource_time_idx
  on public.industrial_world_sales(resource_type, created_at desc);
alter table public.industrial_bankers enable row level security;
alter table public.industrial_world_sales enable row level security;

create or replace function public.enforce_industrial_banker_ownership()
returns trigger language plpgsql set search_path = ''
as $$
declare company_owner bigint; company_job text; user_job text;
begin
  select c.owner_discord_user_id,c.job_type into company_owner,company_job
    from public.industrial_companies c where c.id=new.company_id;
  select u.primary_job into user_job from public.industrial_users u
    where u.discord_user_id=new.owner_discord_user_id;
  if company_owner is distinct from new.owner_discord_user_id
    or company_job is distinct from 'banker' or user_job is distinct from 'banker' then
    raise exception 'industrial banker requires matching user and company' using errcode='23514'; end if;
  return new;
end;
$$;
create trigger industrial_bankers_enforce_ownership before insert or update of owner_discord_user_id,company_id
  on public.industrial_bankers for each row execute function public.enforce_industrial_banker_ownership();
create trigger industrial_bankers_set_updated_at before update on public.industrial_bankers
  for each row execute function public.set_industrial_updated_at();

create or replace function public.ensure_and_refresh_industrial_banker(p_owner_discord_user_id bigint)
returns text language plpgsql security invoker set search_path = ''
as $$
declare user_job text; banker_company bigint; t public.industrial_transports%rowtype; current_time timestamptz;
begin
  select u.primary_job into user_job from public.industrial_users u where u.discord_user_id = p_owner_discord_user_id;
  if user_job is distinct from 'banker' then return 'not_banker'; end if;
  select c.id into banker_company from public.industrial_companies c
    where c.owner_discord_user_id = p_owner_discord_user_id and c.is_first_company and c.job_type = 'banker';
  if banker_company is null then return 'no_banker_company'; end if;
  perform pg_catalog.pg_advisory_xact_lock(p_owner_discord_user_id);
  insert into public.industrial_bankers(owner_discord_user_id, company_id)
    values (p_owner_discord_user_id, banker_company) on conflict (owner_discord_user_id) do nothing;
  perform 1 from public.industrial_bankers b where b.owner_discord_user_id = p_owner_discord_user_id for update;
  current_time := clock_timestamp();
  for t in select x.* from public.industrial_transports x
    where x.receiver_company_id = banker_company and x.transport_type = 'ingot_to_banker'
      and x.status = 'in_transit' and x.arrival_at <= current_time order by x.id for update
  loop
    update public.industrial_transports set status = 'delivered', completed_at = current_time
      where id = t.id and status = 'in_transit';
    if found then
      insert into public.industrial_inventory(owner_discord_user_id, resource_type, quantity)
        values (p_owner_discord_user_id, t.resource_type, t.quantity)
        on conflict (owner_discord_user_id, resource_type) do update
          set quantity = industrial_inventory.quantity + excluded.quantity;
    end if;
  end loop;
  return 'ok';
end;
$$;

create or replace function public.get_or_create_and_refresh_industrial_banker(p_owner_discord_user_id bigint)
returns table(result_status text, current_job text, company_id bigint, company_name text, credits bigint)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text;
begin
  operation_status := public.ensure_and_refresh_industrial_banker(p_owner_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u where u.discord_user_id = p_owner_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status, user_job, null::bigint, null::text, null::bigint; return; end if;
  return query select 'ok', user_job, b.company_id, c.name, u.credits
    from public.industrial_bankers b join public.industrial_companies c on c.id = b.company_id
    join public.industrial_users u on u.discord_user_id = b.owner_discord_user_id
    where b.owner_discord_user_id = p_owner_discord_user_id;
end;
$$;

create or replace function public.industrial_world_ingot_price()
returns bigint language sql stable security invoker set search_path = ''
as $$
  select greatest(50::numeric, least(120::numeric, 80::numeric -
    floor(coalesce(sum(s.quantity), 0)::numeric / 1000)))::bigint
  from public.industrial_world_sales s where s.resource_type = 'iron_ingot'
    and s.created_at >= now() - interval '24 hours';
$$;

create or replace function public.sell_industrial_ingots_to_world(
  p_banker_discord_user_id bigint, p_quantity bigint, p_request_id text
)
returns table(result_status text, current_job text, available_amount bigint, sale_id bigint,
  quantity bigint, unit_price bigint, total_credits bigint, balance_after bigint, created_at timestamptz)
language plpgsql security invoker set search_path = ''
as $$
declare operation_status text; user_job text; banker_company bigint; available bigint;
  price bigint; total bigint; sale public.industrial_world_sales%rowtype;
begin
  if p_quantity not between 1 and 1000000 or p_request_id is null
    or char_length(p_request_id) not between 1 and 80 then raise exception 'invalid world sale' using errcode='22023'; end if;
  perform pg_catalog.pg_advisory_xact_lock(9000000001);
  perform pg_catalog.pg_advisory_xact_lock(p_banker_discord_user_id);
  operation_status := public.ensure_and_refresh_industrial_banker(p_banker_discord_user_id);
  select u.primary_job into user_job from public.industrial_users u where u.discord_user_id = p_banker_discord_user_id;
  if operation_status <> 'ok' then
    return query select operation_status,user_job,null::bigint,null::bigint,null::bigint,
      null::bigint,null::bigint,null::bigint,null::timestamptz; return; end if;
  select s.* into sale from public.industrial_world_sales s where s.request_id = p_request_id;
  if found then
    if sale.banker_discord_user_id <> p_banker_discord_user_id or sale.quantity <> p_quantity then
      raise exception 'request id reused with different world sale parameters' using errcode='23505'; end if;
    return query select 'duplicate',user_job,null::bigint,sale.id,sale.quantity,sale.unit_price,
      sale.total_credits,sale.balance_after,sale.created_at; return; end if;
  select b.company_id into banker_company from public.industrial_bankers b
    where b.owner_discord_user_id = p_banker_discord_user_id for update;
  select i.quantity into available from public.industrial_inventory i
    where i.owner_discord_user_id = p_banker_discord_user_id and i.resource_type='iron_ingot' for update;
  available := coalesce(available,0);
  if available < p_quantity then
    return query select 'insufficient_inventory',user_job,available,null::bigint,null::bigint,
      null::bigint,null::bigint,null::bigint,null::timestamptz; return; end if;
  price := public.industrial_world_ingot_price(); total := price * p_quantity;
  update public.industrial_inventory set quantity=quantity-p_quantity
    where owner_discord_user_id=p_banker_discord_user_id and resource_type='iron_ingot';
  update public.industrial_users set credits=credits+total
    where discord_user_id=p_banker_discord_user_id returning credits into available;
  insert into public.industrial_world_sales(banker_discord_user_id,banker_company_id,
    resource_type,quantity,unit_price,total_credits,balance_after,request_id)
    values(p_banker_discord_user_id,banker_company,'iron_ingot',p_quantity,price,total,available,p_request_id)
    returning * into sale;
  return query select 'ok',user_job,null::bigint,sale.id,sale.quantity,sale.unit_price,
    sale.total_credits,sale.balance_after,sale.created_at;
end;
$$;

create or replace function public.get_industrial_world_market(p_resource_type text)
returns table(current_price bigint, volume_24h bigint, change_24h numeric)
language sql stable security invoker set search_path = ''
as $$
  select public.industrial_world_ingot_price(), coalesce(sum(s.quantity),0)::bigint,
    case when min(s.unit_price) is null or min(s.unit_price)=0 then 0::numeric
      else round((public.industrial_world_ingot_price()-min(s.unit_price))::numeric*100/min(s.unit_price),1) end
  from public.industrial_world_sales s where p_resource_type='iron_ingot'
    and s.created_at >= now()-interval '24 hours';
$$;

revoke all on table public.industrial_bankers, public.industrial_world_sales from public,anon,authenticated;
revoke all on sequence public.industrial_world_sales_id_seq from public,anon,authenticated;
grant select,insert,update on table public.industrial_bankers,public.industrial_world_sales to service_role;
grant usage,select on sequence public.industrial_world_sales_id_seq to service_role;
revoke all on function public.ensure_and_refresh_industrial_banker(bigint) from public,anon,authenticated;
revoke all on function public.enforce_industrial_banker_ownership() from public,anon,authenticated;
revoke all on function public.get_or_create_and_refresh_industrial_banker(bigint) from public,anon,authenticated;
revoke all on function public.industrial_world_ingot_price() from public,anon,authenticated;
revoke all on function public.sell_industrial_ingots_to_world(bigint,bigint,text) from public,anon,authenticated;
revoke all on function public.get_industrial_world_market(text) from public,anon,authenticated;
grant execute on function public.ensure_and_refresh_industrial_banker(bigint) to service_role;
grant execute on function public.get_or_create_and_refresh_industrial_banker(bigint) to service_role;
grant execute on function public.industrial_world_ingot_price() to service_role;
grant execute on function public.sell_industrial_ingots_to_world(bigint,bigint,text) to service_role;
grant execute on function public.get_industrial_world_market(text) to service_role;
