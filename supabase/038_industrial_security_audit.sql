-- Phase 10 : idempotence Mineur, journal actor-aware, audit sources/transferts/sinks.
create table public.industrial_mine_upgrade_requests(
 request_id text primary key check(char_length(request_id) between 1 and 80),
 owner_discord_user_id bigint not null references public.industrial_users(discord_user_id) on delete restrict,
 upgrade_type text not null check(upgrade_type in('storage','production','quality')),
 previous_level integer not null,new_level integer not null,upgrade_cost bigint not null check(upgrade_cost>0),
 wallet_balance bigint not null check(wallet_balance>=0),created_at timestamptz not null default now(),
 check(new_level=previous_level+1));
alter table public.industrial_mine_upgrade_requests enable row level security;

create or replace function public.upgrade_industrial_mine_idempotent(
 p_owner_discord_user_id bigint,p_upgrade_type text,p_request_id text)
returns table(result_status text,current_job text,owner_discord_user_id bigint,company_id bigint,
 company_name text,resource_type text,stock bigint,storage_level integer,production_level integer,
 quality_level integer,production_progress bigint,last_production_at timestamptz,upgrade_type text,
 previous_level integer,new_level integer,upgrade_cost bigint,wallet_balance bigint)
language plpgsql security invoker set search_path=''
as $$declare previous public.industrial_mine_upgrade_requests%rowtype;result record;mine public.industrial_mines%rowtype;title text;job text;begin
 if p_request_id is null or char_length(p_request_id) not between 1 and 80 then raise exception 'invalid request id' using errcode='22023';end if;
 perform pg_catalog.pg_advisory_xact_lock(p_owner_discord_user_id);
 select r.* into previous from public.industrial_mine_upgrade_requests r where r.request_id=p_request_id;
 if found then
  if previous.owner_discord_user_id<>p_owner_discord_user_id or previous.upgrade_type<>p_upgrade_type then raise exception 'request id parameter mismatch' using errcode='23505';end if;
  select m.* into mine from public.industrial_mines m where m.owner_discord_user_id=p_owner_discord_user_id;
  select c.name into title from public.industrial_companies c where c.id=mine.company_id;
  select u.primary_job into job from public.industrial_users u where u.discord_user_id=p_owner_discord_user_id;
  return query select 'ok',job,mine.owner_discord_user_id,mine.company_id,title,mine.resource_type,mine.stock,
   mine.storage_level,mine.production_level,mine.quality_level,mine.production_progress,mine.last_production_at,
   previous.upgrade_type,previous.previous_level,previous.new_level,previous.upgrade_cost,previous.wallet_balance;return;
 end if;
 select * into result from public.upgrade_industrial_mine(p_owner_discord_user_id,p_upgrade_type);
 if result.result_status='ok' then
  insert into public.industrial_mine_upgrade_requests(request_id,owner_discord_user_id,upgrade_type,
   previous_level,new_level,upgrade_cost,wallet_balance)values(p_request_id,p_owner_discord_user_id,p_upgrade_type,
   result.previous_level,result.new_level,result.upgrade_cost,result.wallet_balance);
 end if;
 return query select result.result_status,result.current_job,result.owner_discord_user_id,result.company_id,
  result.company_name,result.resource_type,result.stock,result.storage_level,result.production_level,result.quality_level,
  result.production_progress,result.last_production_at,result.upgrade_type,result.previous_level,result.new_level,
  result.upgrade_cost,result.wallet_balance;
end;$$;

create table public.industrial_transactions(
 id bigserial primary key,transaction_type text not null,
 monetary_effect text not null check(monetary_effect in('source','transfer','sink','none')),
 actor_id bigint references public.industrial_actors(id) on delete restrict,
 counterparty_actor_id bigint references public.industrial_actors(id) on delete restrict,
 resource_type text,quantity bigint check(quantity is null or quantity>=0),
 credits bigint check(credits is null or credits>=0),reference_type text,reference_id bigint,
 metadata jsonb not null default '{}'::jsonb check(pg_column_size(metadata)<=2048),
 created_at timestamptz not null default now());
create index industrial_transactions_type_time_idx on public.industrial_transactions(transaction_type,created_at desc);
create index industrial_transactions_actor_time_idx on public.industrial_transactions(actor_id,created_at desc);
alter table public.industrial_transactions enable row level security;

insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,resource_type,quantity,credits,reference_type,reference_id)
 select 'world_sale','source',a.id,s.resource_type,s.quantity,s.total_credits,'world_sale',s.id
 from public.industrial_world_sales s join public.industrial_actors a on a.discord_user_id=s.banker_discord_user_id;
insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,counterparty_actor_id,resource_type,quantity,credits,reference_type,reference_id)
 select 'market_trade','transfer',t.seller_actor_id,t.buyer_actor_id,t.resource_type,t.quantity,t.total_price,'market_trade',t.id
 from public.industrial_market_trades t;
insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id)
 select 'ai_bootstrap','source',e.actor_id,e.amount,'ai_cash_event',e.id from public.industrial_ai_cash_events e
 where e.event_type='bootstrap_source';
insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id)
 select 'merchant_upgrade','sink',a.id,u.cost,'merchant_upgrade',u.id from public.industrial_merchant_upgrades u
 join public.industrial_actors a on a.discord_user_id=u.owner_discord_user_id;
insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id)
 select 'forge_upgrade','sink',a.id,u.cost,'forge_upgrade',u.id from public.industrial_forge_upgrades u
 join public.industrial_actors a on a.discord_user_id=u.owner_discord_user_id;

create or replace function public.audit_industrial_actor_transaction()
returns trigger language plpgsql set search_path=''
as $$declare actor bigint;counterparty bigint;begin
 if tg_table_name='industrial_world_sales' then
  select a.id into actor from public.industrial_actors a where a.discord_user_id=new.banker_discord_user_id;
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,resource_type,quantity,credits,reference_type,reference_id)
   values('world_sale','source',actor,new.resource_type,new.quantity,new.total_credits,'world_sale',new.id);
 elsif tg_table_name='industrial_market_trades' then
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,counterparty_actor_id,resource_type,quantity,credits,reference_type,reference_id)
   values('market_trade','transfer',new.seller_actor_id,new.buyer_actor_id,new.resource_type,new.quantity,new.total_price,'market_trade',new.id);
 elsif tg_table_name='industrial_ai_cash_events' and new.event_type='bootstrap_source' then
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id)
   values('ai_bootstrap','source',new.actor_id,new.amount,'ai_cash_event',new.id);
 elsif tg_table_name='industrial_delivery_missions' and old.status='open' and new.status='accepted' then
  select a.id into counterparty from public.industrial_actors a where a.discord_user_id=new.courier_discord_user_id;
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,counterparty_actor_id,credits,reference_type,reference_id,metadata)
   values('delivery_commission','transfer',new.merchant_actor_id,counterparty,new.commission_paid,'delivery_mission',new.id,
    jsonb_build_object('merchant_refund',new.merchant_refund));
 elsif tg_table_name='industrial_contracts' and old.status='open' and new.status='completed' then
  select a.id into actor from public.industrial_actors a where a.discord_user_id=new.creator_discord_user_id;
  select a.id into counterparty from public.industrial_actors a where a.discord_user_id=new.accepter_discord_user_id;
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,counterparty_actor_id,resource_type,quantity,credits,reference_type,reference_id)
   values('contract_completion','transfer',actor,counterparty,new.resource_type,new.quantity,new.total_price,'contract',new.id);
 elsif tg_table_name='industrial_mine_upgrade_requests' then
  select a.id into actor from public.industrial_actors a where a.discord_user_id=new.owner_discord_user_id;
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id)
   values('mine_upgrade','sink',actor,new.upgrade_cost,'mine_upgrade',null);
 elsif tg_table_name='industrial_merchant_upgrades' then
  select a.id into actor from public.industrial_actors a where a.discord_user_id=new.owner_discord_user_id;
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id)
   values('merchant_upgrade','sink',actor,new.cost,'merchant_upgrade',new.id);
 elsif tg_table_name='industrial_forge_upgrades' then
  select a.id into actor from public.industrial_actors a where a.discord_user_id=new.owner_discord_user_id;
  insert into public.industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id)
   values('forge_upgrade','sink',actor,new.cost,'forge_upgrade',new.id);
 end if;return new;end;$$;
create trigger industrial_world_sales_actor_audit after insert on public.industrial_world_sales for each row execute function public.audit_industrial_actor_transaction();
create trigger industrial_market_trades_actor_audit after insert on public.industrial_market_trades for each row execute function public.audit_industrial_actor_transaction();
create trigger industrial_ai_cash_actor_audit after insert on public.industrial_ai_cash_events for each row execute function public.audit_industrial_actor_transaction();
create trigger industrial_delivery_actor_audit after update of status on public.industrial_delivery_missions for each row execute function public.audit_industrial_actor_transaction();
create trigger industrial_contract_actor_audit after update of status on public.industrial_contracts for each row execute function public.audit_industrial_actor_transaction();
create trigger industrial_mine_upgrade_actor_audit after insert on public.industrial_mine_upgrade_requests for each row execute function public.audit_industrial_actor_transaction();
create trigger industrial_merchant_upgrade_actor_audit after insert on public.industrial_merchant_upgrades for each row execute function public.audit_industrial_actor_transaction();
create trigger industrial_forge_upgrade_actor_audit after insert on public.industrial_forge_upgrades for each row execute function public.audit_industrial_actor_transaction();

revoke all on table public.industrial_mine_upgrade_requests,public.industrial_transactions from public,anon,authenticated;
revoke all on sequence public.industrial_transactions_id_seq from public,anon,authenticated;
grant select,insert on table public.industrial_mine_upgrade_requests,public.industrial_transactions to service_role;
grant usage,select on sequence public.industrial_transactions_id_seq to service_role;
revoke all on function public.upgrade_industrial_mine_idempotent(bigint,text,text) from public,anon,authenticated;
revoke all on function public.audit_industrial_actor_transaction() from public,anon,authenticated;
grant execute on function public.upgrade_industrial_mine_idempotent(bigint,text,text) to service_role;
