-- Phase 7 : contrats resource_supply avec escrow intégral et expiration lazy.
create table public.industrial_contracts(
  id bigserial primary key,contract_type text not null default 'resource_supply' check(contract_type='resource_supply'),
  creator_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
  accepter_discord_user_id bigint references public.industrial_users(discord_user_id) on delete restrict,
  resource_type text not null check(resource_type in('iron_ore','iron_ingot')),
  quantity bigint not null check(quantity between 1 and 1000000),
  total_price bigint not null check(total_price between 1 and 1000000000),
  escrow_credits bigint not null check(escrow_credits>=0 and escrow_credits<=total_price),
  status text not null default 'open' check(status in('open','completed','cancelled','expired')),
  request_id text not null unique check(char_length(request_id) between 1 and 80),
  accept_request_id text unique check(accept_request_id is null or char_length(accept_request_id) between 1 and 80),
  cancel_request_id text unique check(cancel_request_id is null or char_length(cancel_request_id) between 1 and 80),
  created_at timestamptz not null default now(),expires_at timestamptz not null default(now()+interval '72 hours'),
  completed_at timestamptz,cancelled_at timestamptz,
  check((status='open' and escrow_credits=total_price and accepter_discord_user_id is null)
    or(status='completed' and escrow_credits=0 and accepter_discord_user_id is not null and completed_at is not null)
    or(status in('cancelled','expired') and escrow_credits=0 and accepter_discord_user_id is null and cancelled_at is not null))
);
create index industrial_contracts_open_idx on public.industrial_contracts(status,expires_at) where status='open';
create index industrial_contracts_creator_idx on public.industrial_contracts(creator_discord_user_id,created_at desc);
alter table public.industrial_contracts enable row level security;

create or replace function public.refresh_industrial_contracts()
returns integer language plpgsql security invoker set search_path=''
as $$ declare c public.industrial_contracts%rowtype;n integer:=0;begin
  perform pg_catalog.pg_advisory_xact_lock(9000000003);
  for c in select x.* from public.industrial_contracts x where x.status='open' and x.expires_at<=clock_timestamp()
    order by x.id for update loop
    update public.industrial_users set credits=credits+c.escrow_credits where discord_user_id=c.creator_discord_user_id;
    update public.industrial_contracts as target_contract
      set status='expired',escrow_credits=0,cancelled_at=clock_timestamp()
      where target_contract.id=c.id and target_contract.status='open';n:=n+1;
  end loop;return n;end;$$;

create or replace function public.create_industrial_resource_contract(
 p_creator_discord_user_id bigint,p_resource_type text,p_quantity bigint,p_total_price bigint,p_request_id text)
returns table(result_status text,available_amount bigint,id bigint,creator_discord_user_id bigint,
 accepter_discord_user_id bigint,resource_type text,quantity bigint,total_price bigint,status text,expires_at timestamptz)
language plpgsql security invoker set search_path=''
as $$ declare c public.industrial_contracts%rowtype;balance bigint;open_count integer;begin
 if p_resource_type not in('iron_ore','iron_ingot') or p_quantity not between 1 and 1000000
  or p_total_price not between 1 and 1000000000 or p_request_id is null or char_length(p_request_id) not between 1 and 80
  then raise exception 'invalid contract' using errcode='22023';end if;
 perform public.refresh_industrial_contracts();perform pg_catalog.pg_advisory_xact_lock(p_creator_discord_user_id);
 select x.* into c from public.industrial_contracts x where x.request_id=p_request_id;
 if found then
  if c.creator_discord_user_id<>p_creator_discord_user_id or c.resource_type<>p_resource_type
    or c.quantity<>p_quantity or c.total_price<>p_total_price then raise exception 'request id parameter mismatch' using errcode='23505';end if;
  return query select 'duplicate',null::bigint,c.id,c.creator_discord_user_id,c.accepter_discord_user_id,
    c.resource_type,c.quantity,c.total_price,c.status,c.expires_at;return;end if;
 select count(*) into open_count from public.industrial_contracts x where x.creator_discord_user_id=p_creator_discord_user_id and x.status='open';
 if open_count>=10 then return query select 'contract_limit',null::bigint,null::bigint,null::bigint,
  null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 select u.credits into balance from public.industrial_users u where u.discord_user_id=p_creator_discord_user_id for update;
 if coalesce(balance,0)<p_total_price then return query select 'insufficient_funds',coalesce(balance,0),null::bigint,
  null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 update public.industrial_users set credits=credits-p_total_price where discord_user_id=p_creator_discord_user_id;
 insert into public.industrial_contracts(creator_discord_user_id,resource_type,quantity,total_price,escrow_credits,request_id)
 values(p_creator_discord_user_id,p_resource_type,p_quantity,p_total_price,p_total_price,p_request_id) returning * into c;
 return query select 'ok',balance-p_total_price,c.id,c.creator_discord_user_id,c.accepter_discord_user_id,
  c.resource_type,c.quantity,c.total_price,c.status,c.expires_at;end;$$;

create or replace function public.accept_industrial_resource_contract(
 p_accepter_discord_user_id bigint,p_contract_id bigint,p_request_id text)
returns table(result_status text,available_amount bigint,id bigint,creator_discord_user_id bigint,
 accepter_discord_user_id bigint,resource_type text,quantity bigint,total_price bigint,status text,expires_at timestamptz)
language plpgsql security invoker set search_path=''
as $$ declare c public.industrial_contracts%rowtype;available bigint;lock_id bigint;begin
 if p_contract_id<1 or p_request_id is null or char_length(p_request_id) not between 1 and 80 then raise exception 'invalid accept' using errcode='22023';end if;
 perform public.refresh_industrial_contracts();select x.* into c from public.industrial_contracts x where x.id=p_contract_id;
 if not found then return query select 'not_found',null::bigint,null::bigint,null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 for lock_id in select distinct v from unnest(array[c.creator_discord_user_id,p_accepter_discord_user_id])v order by v loop perform pg_catalog.pg_advisory_xact_lock(lock_id);end loop;
 select x.* into c from public.industrial_contracts x where x.id=p_contract_id for update;
 if c.accept_request_id=p_request_id and c.status='completed' then return query select 'duplicate',null::bigint,c.id,c.creator_discord_user_id,c.accepter_discord_user_id,c.resource_type,c.quantity,c.total_price,c.status,c.expires_at;return;end if;
 if c.status<>'open' then return query select 'already_closed',null::bigint,null::bigint,null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 if c.creator_discord_user_id=p_accepter_discord_user_id then return query select 'own_contract',null::bigint,null::bigint,null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 select i.quantity into available from public.industrial_inventory i where i.owner_discord_user_id=p_accepter_discord_user_id and i.resource_type=c.resource_type for update;available:=coalesce(available,0);
 if available<c.quantity then return query select 'insufficient_inventory',available,null::bigint,null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 update public.industrial_inventory as target_inventory
  set quantity=target_inventory.quantity-c.quantity
  where target_inventory.owner_discord_user_id=p_accepter_discord_user_id
    and target_inventory.resource_type=c.resource_type;
 insert into public.industrial_inventory(owner_discord_user_id,resource_type,quantity) values(c.creator_discord_user_id,c.resource_type,c.quantity)
  on conflict on constraint industrial_inventory_pkey do update
   set quantity=industrial_inventory.quantity+excluded.quantity;
 update public.industrial_users set credits=credits+c.escrow_credits where discord_user_id=p_accepter_discord_user_id;
 update public.industrial_contracts as target_contract
  set status='completed',escrow_credits=0,accepter_discord_user_id=p_accepter_discord_user_id,
  accept_request_id=p_request_id,completed_at=clock_timestamp()
  where target_contract.id=c.id returning * into c;
 return query select 'ok',available-c.quantity,c.id,c.creator_discord_user_id,c.accepter_discord_user_id,c.resource_type,c.quantity,c.total_price,c.status,c.expires_at;end;$$;

create or replace function public.cancel_industrial_resource_contract(p_creator_discord_user_id bigint,p_contract_id bigint,p_request_id text)
returns table(result_status text,id bigint,creator_discord_user_id bigint,accepter_discord_user_id bigint,
 resource_type text,quantity bigint,total_price bigint,status text,expires_at timestamptz)
language plpgsql security invoker set search_path=''
as $$declare c public.industrial_contracts%rowtype;begin
 perform public.refresh_industrial_contracts();perform pg_catalog.pg_advisory_xact_lock(p_creator_discord_user_id);select x.* into c from public.industrial_contracts x where x.id=p_contract_id for update;
 if not found then return query select 'not_found',null::bigint,null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 if c.creator_discord_user_id<>p_creator_discord_user_id then return query select 'not_owner',null::bigint,null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 if c.cancel_request_id=p_request_id and c.status='cancelled' then return query select 'duplicate',c.id,c.creator_discord_user_id,c.accepter_discord_user_id,c.resource_type,c.quantity,c.total_price,c.status,c.expires_at;return;end if;
 if c.status<>'open' then return query select 'already_closed',null::bigint,null::bigint,null::bigint,null::text,null::bigint,null::bigint,null::text,null::timestamptz;return;end if;
 update public.industrial_users set credits=credits+c.escrow_credits where discord_user_id=p_creator_discord_user_id;
 update public.industrial_contracts as target_contract
  set status='cancelled',escrow_credits=0,cancel_request_id=p_request_id,cancelled_at=clock_timestamp()
  where target_contract.id=c.id returning * into c;
 return query select 'ok',c.id,c.creator_discord_user_id,c.accepter_discord_user_id,c.resource_type,c.quantity,c.total_price,c.status,c.expires_at;end;$$;

create or replace function public.get_industrial_resource_contracts(p_discord_user_id bigint,p_mine boolean)
returns setof public.industrial_contracts language plpgsql security invoker set search_path=''
as $$begin perform public.refresh_industrial_contracts();return query select c.* from public.industrial_contracts c
 where (p_mine and c.creator_discord_user_id=p_discord_user_id)or(not p_mine and c.status='open') order by c.created_at desc limit 20;end;$$;

revoke all on table public.industrial_contracts from public,anon,authenticated;
revoke all on sequence public.industrial_contracts_id_seq from public,anon,authenticated;
grant select,insert,update on table public.industrial_contracts to service_role;grant usage,select on sequence public.industrial_contracts_id_seq to service_role;
revoke all on function public.refresh_industrial_contracts(),public.create_industrial_resource_contract(bigint,text,bigint,bigint,text),
 public.accept_industrial_resource_contract(bigint,bigint,text),public.cancel_industrial_resource_contract(bigint,bigint,text),
 public.get_industrial_resource_contracts(bigint,boolean) from public,anon,authenticated;
grant execute on function public.refresh_industrial_contracts(),public.create_industrial_resource_contract(bigint,text,bigint,bigint,text),
 public.accept_industrial_resource_contract(bigint,bigint,text),public.cancel_industrial_resource_contract(bigint,bigint,text),
 public.get_industrial_resource_contracts(bigint,boolean) to service_role;
