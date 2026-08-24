-- Identités actor-aware pour ordres et trades, sans activer encore le matching IA.
alter table public.industrial_market_orders
 add column owner_actor_id bigint references public.industrial_actors(id) on delete restrict;
update public.industrial_market_orders o set owner_actor_id=a.id from public.industrial_actors a
 where a.discord_user_id=o.owner_discord_user_id and o.owner_actor_id is null;
do $$begin if exists(select 1 from public.industrial_market_orders where owner_actor_id is null)
 then raise exception 'market order actor backfill incomplete';end if;end;$$;
alter table public.industrial_market_orders alter column owner_actor_id set not null;
alter table public.industrial_market_orders alter column owner_discord_user_id drop not null;
alter table public.industrial_market_orders alter column company_id drop not null;
create index industrial_market_orders_actor_idx on public.industrial_market_orders(owner_actor_id,status,created_at);

create or replace function public.enforce_industrial_market_order_actor()
returns trigger language plpgsql set search_path=''
as $$declare selected_actor public.industrial_actors%rowtype;begin
 if new.owner_actor_id is null and new.owner_discord_user_id is not null then
  select a.* into selected_actor from public.industrial_actors a where a.discord_user_id=new.owner_discord_user_id;
  new.owner_actor_id:=selected_actor.id;
 else select a.* into selected_actor from public.industrial_actors a where a.id=new.owner_actor_id;end if;
 if selected_actor.id is null then raise exception 'invalid market actor' using errcode='23514';end if;
 if selected_actor.actor_type='player' then
  if new.owner_discord_user_id is null then new.owner_discord_user_id:=selected_actor.discord_user_id;end if;
  if new.owner_discord_user_id is distinct from selected_actor.discord_user_id or new.company_id is null
   then raise exception 'player market actor mismatch' using errcode='23514';end if;
 elsif new.owner_discord_user_id is not null or new.company_id is not null then
  raise exception 'AI market order has player identity' using errcode='23514';end if;
 return new;end;$$;
create trigger industrial_market_orders_enforce_actor before insert or update of owner_actor_id,owner_discord_user_id,company_id
 on public.industrial_market_orders for each row execute function public.enforce_industrial_market_order_actor();

alter table public.industrial_market_trades
 add column seller_actor_id bigint references public.industrial_actors(id) on delete restrict,
 add column buyer_actor_id bigint references public.industrial_actors(id) on delete restrict;
update public.industrial_market_trades t set
 seller_actor_id=(select a.id from public.industrial_actors a where a.discord_user_id=t.seller_discord_user_id),
 buyer_actor_id=(select a.id from public.industrial_actors a where a.discord_user_id=t.buyer_discord_user_id)
where seller_actor_id is null or buyer_actor_id is null;
do $$begin if exists(select 1 from public.industrial_market_trades where seller_actor_id is null or buyer_actor_id is null)
 then raise exception 'market trade actor backfill incomplete';end if;end;$$;
alter table public.industrial_market_trades alter column seller_actor_id set not null;
alter table public.industrial_market_trades alter column buyer_actor_id set not null;
alter table public.industrial_market_trades alter column seller_discord_user_id drop not null;
alter table public.industrial_market_trades alter column buyer_discord_user_id drop not null;
alter table public.industrial_market_trades add constraint industrial_market_trades_distinct_actors
 check(seller_actor_id<>buyer_actor_id);
create index industrial_market_trades_actor_time_idx on public.industrial_market_trades(seller_actor_id,buyer_actor_id,created_at desc);

create or replace function public.enforce_industrial_market_trade_actors()
returns trigger language plpgsql set search_path=''
as $$declare seller public.industrial_actors%rowtype;buyer public.industrial_actors%rowtype;begin
 if new.seller_actor_id is null and new.seller_discord_user_id is not null then select a.* into seller from public.industrial_actors a where a.discord_user_id=new.seller_discord_user_id;new.seller_actor_id:=seller.id;else select a.* into seller from public.industrial_actors a where a.id=new.seller_actor_id;end if;
 if new.buyer_actor_id is null and new.buyer_discord_user_id is not null then select a.* into buyer from public.industrial_actors a where a.discord_user_id=new.buyer_discord_user_id;new.buyer_actor_id:=buyer.id;else select a.* into buyer from public.industrial_actors a where a.id=new.buyer_actor_id;end if;
 if seller.id is null or buyer.id is null or seller.id=buyer.id then raise exception 'invalid market trade actors' using errcode='23514';end if;
 if seller.actor_type='player' and new.seller_discord_user_id is distinct from seller.discord_user_id then raise exception 'seller actor mismatch' using errcode='23514';end if;
 if buyer.actor_type='player' and new.buyer_discord_user_id is distinct from buyer.discord_user_id then raise exception 'buyer actor mismatch' using errcode='23514';end if;
 if seller.actor_type='ai' then new.seller_discord_user_id:=null;end if;
 if buyer.actor_type='ai' then new.buyer_discord_user_id:=null;end if;
 return new;end;$$;
create trigger industrial_market_trades_enforce_actors before insert or update of seller_actor_id,buyer_actor_id,seller_discord_user_id,buyer_discord_user_id
 on public.industrial_market_trades for each row execute function public.enforce_industrial_market_trade_actors();

revoke all on function public.enforce_industrial_market_order_actor(),public.enforce_industrial_market_trade_actors()
 from public,anon,authenticated;
