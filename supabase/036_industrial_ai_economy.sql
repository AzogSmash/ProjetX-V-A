-- IA réellement actives : budget explicite, production lazy à 60 %, achats et transports actor-aware.
alter table public.industrial_market_trades drop constraint industrial_market_trades_resource_type_check;
alter table public.industrial_market_trades add constraint industrial_market_trades_resource_type_check
 check(resource_type in('iron_ore','iron_ingot'));
alter table public.industrial_market_trades alter column sell_order_id drop not null;
alter table public.industrial_market_trades alter column buy_order_id drop not null;

alter table public.industrial_delivery_missions add column merchant_actor_id bigint references public.industrial_actors(id) on delete restrict;
update public.industrial_delivery_missions m set merchant_actor_id=a.id from public.industrial_actors a
 where a.discord_user_id=m.merchant_discord_user_id and m.merchant_actor_id is null;
do $$begin if exists(select 1 from public.industrial_delivery_missions where merchant_actor_id is null)
 then raise exception 'delivery actor backfill incomplete';end if;end;$$;
alter table public.industrial_delivery_missions alter column merchant_actor_id set not null;
alter table public.industrial_delivery_missions alter column merchant_discord_user_id drop not null;
create index industrial_delivery_missions_merchant_actor_idx on public.industrial_delivery_missions(merchant_actor_id,status,created_at);

create table public.industrial_ai_accounts(
 actor_id bigint primary key references public.industrial_actors(id) on delete restrict,
 credits bigint not null default 0 check(credits>=0),created_at timestamptz not null default now(),updated_at timestamptz not null default now());
create table public.industrial_ai_cash_events(
 id bigserial primary key,actor_id bigint not null references public.industrial_actors(id) on delete restrict,
 event_type text not null check(event_type in('bootstrap_source','player_payment_transfer','delivery_escrow','delivery_refund')),
 amount bigint not null check(amount<>0),balance_after bigint not null check(balance_after>=0),
 reference_type text,reference_id bigint,created_at timestamptz not null default now());
create table public.industrial_ai_production(
 actor_id bigint primary key references public.industrial_actors(id) on delete restrict,
 resource_type text not null check(resource_type in('iron_ore','iron_ingot')),
 rate_per_hour integer not null default 6 check(rate_per_hour=6),capacity bigint not null default 1000 check(capacity=1000),
 total_produced bigint not null default 0 check(total_produced>=0),
 production_progress integer not null default 0 check(production_progress between 0 and 3599),
 last_production_at timestamptz not null default now(),updated_at timestamptz not null default now());
create table public.industrial_ai_supply_purchases(
 id bigserial primary key,buyer_actor_id bigint not null references public.industrial_actors(id) on delete restrict,
 producer_actor_id bigint not null references public.industrial_actors(id) on delete restrict,
 operator_actor_id bigint not null references public.industrial_actors(id) on delete restrict,
 transport_id bigint not null unique references public.industrial_transports(id) on delete restrict,
 resource_type text not null check(resource_type in('iron_ore','iron_ingot')),
 quantity bigint not null check(quantity between 1 and 1000),unit_price bigint not null check(unit_price in(12,100)),
 total_price bigint not null check(total_price=quantity*unit_price),commission_escrow bigint not null check(commission_escrow between 20 and 5000),
 request_id text not null unique check(char_length(request_id) between 1 and 80),created_at timestamptz not null default now());
alter table public.industrial_ai_accounts enable row level security;
alter table public.industrial_ai_cash_events enable row level security;
alter table public.industrial_ai_production enable row level security;
alter table public.industrial_ai_supply_purchases enable row level security;

create or replace function public.enforce_industrial_delivery_actor()
returns trigger language plpgsql set search_path=''
as $$declare selected_actor public.industrial_actors%rowtype;begin
 if new.merchant_actor_id is null and new.merchant_discord_user_id is not null then
  select a.* into selected_actor from public.industrial_actors a where a.discord_user_id=new.merchant_discord_user_id;
  new.merchant_actor_id:=selected_actor.id;
 else select a.* into selected_actor from public.industrial_actors a where a.id=new.merchant_actor_id;end if;
 if selected_actor.id is null then raise exception 'invalid delivery merchant actor' using errcode='23514';end if;
 if selected_actor.actor_type='player' and new.merchant_discord_user_id is distinct from selected_actor.discord_user_id
  then raise exception 'delivery actor mismatch' using errcode='23514';end if;
 if selected_actor.actor_type='ai' then new.merchant_discord_user_id:=null;end if;
 return new;end;$$;
create trigger industrial_delivery_missions_enforce_actor before insert or update of merchant_actor_id,merchant_discord_user_id
 on public.industrial_delivery_missions for each row execute function public.enforce_industrial_delivery_actor();

create or replace function public.refund_industrial_ai_delivery()
returns trigger language plpgsql set search_path=''
as $$declare selected_actor public.industrial_actors%rowtype;resulting_balance bigint;refund bigint;begin
 if old.status='open' and new.status in('accepted','refunded') then
  select a.* into selected_actor from public.industrial_actors a where a.id=new.merchant_actor_id;
  if selected_actor.actor_type='ai' then
   refund:=case new.status when 'accepted' then new.merchant_refund else new.commission_max end;
   update public.industrial_ai_accounts set credits=credits+refund,updated_at=clock_timestamp()
    where actor_id=new.merchant_actor_id returning credits into resulting_balance;
   if refund>0 then
    insert into public.industrial_ai_cash_events(actor_id,event_type,amount,balance_after,reference_type,reference_id)
     values(new.merchant_actor_id,'delivery_refund',refund,resulting_balance,'delivery_mission',new.id);
   end if;
  end if;
 end if;return new;end;$$;
create trigger industrial_delivery_missions_refund_ai before update of status on public.industrial_delivery_missions
 for each row execute function public.refund_industrial_ai_delivery();

create or replace function public.ensure_industrial_ai_economy()
returns integer language plpgsql security invoker set search_path=''
as $$declare company public.industrial_ai_companies%rowtype;actor_id bigint;inserted_count integer:=0;begin
 perform pg_catalog.pg_advisory_xact_lock(9000000002);
 perform public.evaluate_industrial_ai_companies();
 for company in select c.* from public.industrial_ai_companies c where c.enabled order by c.id loop
  insert into public.industrial_actors(actor_type,ai_company_id)values('ai',company.id)
   on conflict(ai_company_id)do nothing;
  select a.id into actor_id from public.industrial_actors a where a.ai_company_id=company.id;
  insert into public.industrial_ai_accounts(actor_id,credits)values(actor_id,25000)on conflict(actor_id)do nothing;
  if found then
   insert into public.industrial_ai_cash_events(actor_id,event_type,amount,balance_after,reference_type,reference_id)
    values(actor_id,'bootstrap_source',25000,25000,'ai_company',company.id);inserted_count:=inserted_count+1;
  end if;
  if company.job_type in('miner','blacksmith') then
   insert into public.industrial_ai_production(actor_id,resource_type)
    values(actor_id,case company.job_type when 'miner' then 'iron_ore' else 'iron_ingot' end)
    on conflict(actor_id)do nothing;
  end if;
 end loop;return inserted_count;end;$$;

create or replace function public.refresh_industrial_ai_production()
returns bigint language plpgsql security invoker set search_path=''
as $$declare state public.industrial_ai_production%rowtype;now_at timestamptz;elapsed bigint;
 numerator bigint;produced bigint;available bigint;added bigint;total_added bigint:=0;
 lock_actor bigint;input_actor bigint;input_available bigint;begin
 perform public.ensure_industrial_ai_economy();now_at:=clock_timestamp();
 for lock_actor in select p.actor_id from public.industrial_ai_production p join public.industrial_actors a on a.id=p.actor_id
  join public.industrial_ai_companies c on c.id=a.ai_company_id where c.enabled order by p.actor_id
 loop perform pg_catalog.pg_advisory_xact_lock(-lock_actor);end loop;
 for state in select p.* from public.industrial_ai_production p join public.industrial_actors a on a.id=p.actor_id
  join public.industrial_ai_companies c on c.id=a.ai_company_id where c.enabled order by p.actor_id for update loop
  select coalesce(i.quantity,0) into available from public.industrial_inventory i
   where i.actor_id=state.actor_id and i.resource_type=state.resource_type for update;
  elapsed:=greatest(0,floor(extract(epoch from(now_at-state.last_production_at)))::bigint);
  numerator:=elapsed*state.rate_per_hour+state.production_progress;produced:=numerator/3600;
  if state.resource_type='iron_ingot' then
   select a.id into input_actor from public.industrial_actors a join public.industrial_ai_companies c on c.id=a.ai_company_id
    where c.job_type='miner' and c.enabled limit 1;
   select coalesce(i.quantity,0) into input_available from public.industrial_inventory i
    where i.actor_id=input_actor and i.resource_type='iron_ore' for update;
   added:=least(produced,greatest(0,state.capacity-available),coalesce(input_available,0));
   if added>0 then update public.industrial_inventory set quantity=quantity-added
    where actor_id=input_actor and resource_type='iron_ore';end if;
  else added:=least(produced,greatest(0,state.capacity-available));end if;
  if added>0 then
   insert into public.industrial_inventory(actor_id,owner_discord_user_id,resource_type,quantity)
    values(state.actor_id,null,state.resource_type,added)
    on conflict(actor_id,resource_type)do update set quantity=industrial_inventory.quantity+excluded.quantity;
  end if;
  update public.industrial_ai_production set total_produced=total_produced+added,
   production_progress=case when available+added>=state.capacity then 0 else (numerator%3600)::integer end,
   last_production_at=now_at,updated_at=now_at where actor_id=state.actor_id;total_added:=total_added+added;
 end loop;return total_added;end;$$;

create or replace function public.purchase_industrial_ai_supply(
 p_buyer_discord_user_id bigint,p_resource_type text,p_quantity bigint,p_request_id text)
returns table(result_status text,available_amount bigint,purchase_id bigint,quantity bigint,
 unit_price bigint,total_price bigint,transport_id bigint,arrival_at timestamptz)
language plpgsql security invoker set search_path=''
as $$declare buyer_actor bigint;producer_actor bigint;operator_actor bigint;receiver_company bigint;
 buyer_job text;price bigint;total bigint;fee bigint;stock bigint;wallet bigint;operator_balance bigint;
 free_slot integer;now_at timestamptz;transport public.industrial_transports%rowtype;
 purchase public.industrial_ai_supply_purchases%rowtype;mission_id bigint;lock_actor bigint;ai_balance bigint;begin
 if p_resource_type not in('iron_ore','iron_ingot') or p_quantity not between 1 and 1000
  or p_request_id is null or char_length(p_request_id) not between 1 and 80 then raise exception 'invalid AI supply' using errcode='22023';end if;
 perform pg_catalog.pg_advisory_xact_lock(9000000004);perform public.refresh_industrial_ai_production();
 select p.* into purchase from public.industrial_ai_supply_purchases p where p.request_id=p_request_id;
 if found then
  if purchase.buyer_actor_id is distinct from(
      select a.id from public.industrial_actors a where a.discord_user_id=p_buyer_discord_user_id)
     or purchase.resource_type<>p_resource_type or purchase.quantity<>p_quantity then
   raise exception 'request id parameter mismatch' using errcode='23505';
  end if;
  select t.* into transport from public.industrial_transports t where t.id=purchase.transport_id;
  return query select 'duplicate',null::bigint,purchase.id,purchase.quantity,purchase.unit_price,purchase.total_price,transport.id,transport.arrival_at;return;end if;
 select a.id,u.primary_job into buyer_actor,buyer_job from public.industrial_users u join public.industrial_actors a on a.discord_user_id=u.discord_user_id
  where u.discord_user_id=p_buyer_discord_user_id;
 if (p_resource_type='iron_ore' and buyer_job is distinct from 'blacksmith')or(p_resource_type='iron_ingot' and buyer_job is distinct from 'banker')then
  return query select 'wrong_job',null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::timestamptz;return;end if;
 select a.id into producer_actor from public.industrial_actors a join public.industrial_ai_companies c on c.id=a.ai_company_id
  where c.enabled and c.job_type=case p_resource_type when 'iron_ore' then 'miner' else 'blacksmith' end limit 1;
 select a.id into operator_actor from public.industrial_actors a join public.industrial_ai_companies c on c.id=a.ai_company_id
  where c.enabled and c.job_type='merchant' limit 1;
 if producer_actor is null or operator_actor is null then return query select 'ai_unavailable',null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::timestamptz;return;end if;
 for lock_actor in select distinct v from unnest(array[buyer_actor,producer_actor,operator_actor])v order by v
  loop perform pg_catalog.pg_advisory_xact_lock(-lock_actor);end loop;
 select c.id into receiver_company from public.industrial_companies c where c.owner_discord_user_id=p_buyer_discord_user_id
  and c.is_first_company and c.job_type=buyer_job;
 price:=case p_resource_type when 'iron_ore' then 12 else 100 end;total:=price*p_quantity;
 fee:=public.industrial_delivery_commission_max(p_quantity);
 select u.credits into wallet from public.industrial_users u where u.discord_user_id=p_buyer_discord_user_id for update;
 if wallet<total then return query select 'insufficient_funds',wallet,null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::timestamptz;return;end if;
 select i.quantity into stock from public.industrial_inventory i where i.actor_id=producer_actor and i.resource_type=p_resource_type for update;stock:=coalesce(stock,0);
 if stock<p_quantity then return query select 'insufficient_ai_stock',stock,null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::timestamptz;return;end if;
 select a.credits into operator_balance from public.industrial_ai_accounts a where a.actor_id=operator_actor for update;
 if operator_balance<fee then return query select 'ai_unavailable',operator_balance,null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::timestamptz;return;end if;
 select 1 into free_slot where not exists(select 1 from public.industrial_transports t where t.operator_actor_id=operator_actor and t.truck_slot=1 and t.status='in_transit');
 if free_slot is null then return query select 'ai_truck_busy',null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::bigint,null::timestamptz;return;end if;
 update public.industrial_users set credits=credits-total where discord_user_id=p_buyer_discord_user_id;
 update public.industrial_ai_accounts set credits=credits+total where actor_id=producer_actor returning credits into ai_balance;
 insert into public.industrial_ai_cash_events(actor_id,event_type,amount,balance_after,reference_type)
  values(producer_actor,'player_payment_transfer',total,ai_balance,'ai_supply');
 update public.industrial_ai_accounts set credits=credits-fee where actor_id=operator_actor returning credits into ai_balance;
 insert into public.industrial_ai_cash_events(actor_id,event_type,amount,balance_after,reference_type)
  values(operator_actor,'delivery_escrow',-fee,ai_balance,'ai_supply');
 update public.industrial_inventory set quantity=quantity-p_quantity where actor_id=producer_actor and resource_type=p_resource_type;
 now_at:=clock_timestamp();
 insert into public.industrial_transports(sender_company_id,receiver_company_id,merchant_discord_user_id,
  sender_actor_id,receiver_actor_id,operator_actor_id,transport_type,resource_type,quantity,departure_at,arrival_at,
  original_duration_seconds,current_duration_seconds,status,truck_slot,request_id)
 values(null,receiver_company,null,producer_actor,buyer_actor,operator_actor,
  case p_resource_type when 'iron_ore' then 'ore_to_blacksmith' else 'ingot_to_banker' end,p_resource_type,p_quantity,
  now_at,now_at+interval '60 minutes',3600,3600,'in_transit',1,p_request_id)returning * into transport;
 insert into public.industrial_delivery_missions(transport_id,merchant_discord_user_id,merchant_actor_id,resource_type,quantity,commission_max,escrow_remaining)
  values(transport.id,null,operator_actor,p_resource_type,p_quantity,fee,fee)returning id into mission_id;
 insert into public.industrial_market_trades(resource_type,quantity,unit_price,seller_discord_user_id,buyer_discord_user_id,
  seller_actor_id,buyer_actor_id,sell_order_id,buy_order_id)
 values(p_resource_type,p_quantity,price,null,p_buyer_discord_user_id,producer_actor,buyer_actor,null,null);
 insert into public.industrial_ai_supply_purchases(buyer_actor_id,producer_actor_id,operator_actor_id,transport_id,
  resource_type,quantity,unit_price,total_price,commission_escrow,request_id)
 values(buyer_actor,producer_actor,operator_actor,transport.id,p_resource_type,p_quantity,price,total,fee,p_request_id)returning * into purchase;
 return query select 'ok',wallet-total,purchase.id,purchase.quantity,purchase.unit_price,purchase.total_price,transport.id,transport.arrival_at;
end;$$;

revoke all on table public.industrial_ai_accounts,public.industrial_ai_cash_events,public.industrial_ai_production,
 public.industrial_ai_supply_purchases from public,anon,authenticated;
revoke all on sequence public.industrial_ai_cash_events_id_seq,public.industrial_ai_supply_purchases_id_seq from public,anon,authenticated;
grant select,insert,update on table public.industrial_ai_accounts,public.industrial_ai_production,public.industrial_ai_supply_purchases to service_role;
grant select,insert on table public.industrial_ai_cash_events to service_role;
grant usage,select on sequence public.industrial_ai_cash_events_id_seq,public.industrial_ai_supply_purchases_id_seq to service_role;
revoke all on function public.enforce_industrial_delivery_actor(),public.refund_industrial_ai_delivery() from public,anon,authenticated;
revoke all on function public.ensure_industrial_ai_economy(),public.refresh_industrial_ai_production(),
 public.purchase_industrial_ai_supply(bigint,text,bigint,text) from public,anon,authenticated;
grant execute on function public.ensure_industrial_ai_economy(),public.refresh_industrial_ai_production(),
 public.purchase_industrial_ai_supply(bigint,text,bigint,text) to service_role;
