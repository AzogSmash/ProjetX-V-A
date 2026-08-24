-- Phase 9 : événements de production et statistiques séparées joueurs / IA / total.
create table public.industrial_resource_events(
 id bigserial primary key,actor_id bigint not null references public.industrial_actors(id) on delete restrict,
 actor_type text not null check(actor_type in('player','ai')),
 event_type text not null check(event_type in('mine_production','forge_production')),
 resource_type text not null check(resource_type in('iron_ore','iron_ingot')),
 quantity bigint not null check(quantity>0),created_at timestamptz not null default now());
create index industrial_resource_events_stats_idx on public.industrial_resource_events(actor_type,event_type,created_at desc);
alter table public.industrial_resource_events enable row level security;

create or replace function public.audit_industrial_player_production()
returns trigger language plpgsql set search_path=''
as $$declare selected_actor bigint;begin
 if tg_table_name='industrial_mines' and new.stock>old.stock then
  select a.id into selected_actor from public.industrial_actors a where a.discord_user_id=new.owner_discord_user_id;
  insert into public.industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity)
   values(selected_actor,'player','mine_production',new.resource_type,new.stock-old.stock);
 elsif tg_table_name='industrial_forge_jobs' and old.status='processing' and new.status='completed' then
  select a.id into selected_actor from public.industrial_actors a where a.discord_user_id=new.owner_discord_user_id;
  insert into public.industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity)
   values(selected_actor,'player','forge_production',new.resource_output,new.output_quantity);
 end if;return new;end;$$;
create trigger industrial_mines_actor_production_audit after update of stock on public.industrial_mines
 for each row execute function public.audit_industrial_player_production();
create trigger industrial_forge_actor_production_audit after update of status on public.industrial_forge_jobs
 for each row execute function public.audit_industrial_player_production();

create or replace function public.audit_industrial_ai_production()
returns trigger language plpgsql set search_path=''
as $$begin
 if new.total_produced>old.total_produced then
  insert into public.industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity)
   values(new.actor_id,'ai',case new.resource_type when 'iron_ore' then 'mine_production' else 'forge_production' end,
    new.resource_type,new.total_produced-old.total_produced);end if;return new;end;$$;
create trigger industrial_ai_production_audit after update of total_produced on public.industrial_ai_production
 for each row execute function public.audit_industrial_ai_production();

create or replace function public.get_industrial_actor_economy_stats()
returns table(player_credits bigint,ai_credits bigint,player_ore bigint,ai_ore bigint,
 player_ingots bigint,ai_ingots bigint,ai_ore_percent numeric,ai_ingot_percent numeric,
 market_volume bigint,market_average_price numeric,ai_market_percent numeric,
 active_transports bigint,average_delivery_minutes numeric,ai_transport_percent numeric,
 world_price bigint,world_price_change_percent numeric,active_contracts bigint,
 player_actors bigint,ai_actors bigint,active_player_companies bigint,active_ai_companies bigint,
 active_miners bigint,active_merchants bigint,active_blacksmiths bigint,active_bankers bigint)
language sql stable security invoker set search_path=''
as $$select
 (select coalesce(sum(u.credits),0)::bigint from public.industrial_users u),
 (select coalesce(sum(a.credits),0)::bigint from public.industrial_ai_accounts a),
 (select coalesce(sum(e.quantity),0)::bigint from public.industrial_resource_events e where e.actor_type='player' and e.resource_type='iron_ore' and e.created_at>=now()-interval '24 hours'),
 (select coalesce(sum(e.quantity),0)::bigint from public.industrial_resource_events e where e.actor_type='ai' and e.resource_type='iron_ore' and e.created_at>=now()-interval '24 hours'),
 (select coalesce(sum(e.quantity),0)::bigint from public.industrial_resource_events e where e.actor_type='player' and e.resource_type='iron_ingot' and e.created_at>=now()-interval '24 hours'),
 (select coalesce(sum(e.quantity),0)::bigint from public.industrial_resource_events e where e.actor_type='ai' and e.resource_type='iron_ingot' and e.created_at>=now()-interval '24 hours'),
 (select coalesce(round(100::numeric*sum(e.quantity)filter(where e.actor_type='ai')::numeric/nullif(sum(e.quantity),0),1),0)
  from public.industrial_resource_events e where e.resource_type='iron_ore' and e.created_at>=now()-interval '24 hours'),
 (select coalesce(round(100::numeric*sum(e.quantity)filter(where e.actor_type='ai')::numeric/nullif(sum(e.quantity),0),1),0)
  from public.industrial_resource_events e where e.resource_type='iron_ingot' and e.created_at>=now()-interval '24 hours'),
 (select coalesce(sum(t.quantity),0)::bigint from public.industrial_market_trades t where t.created_at>=now()-interval '24 hours'),
 (select coalesce(round(sum(t.total_price)::numeric/nullif(sum(t.quantity),0),2),0)
  from public.industrial_market_trades t where t.created_at>=now()-interval '24 hours'),
 (select coalesce(round(100::numeric*sum(t.quantity)filter(where s.actor_type='ai' or b.actor_type='ai')::numeric/nullif(sum(t.quantity),0),1),0)
  from public.industrial_market_trades t join public.industrial_actors s on s.id=t.seller_actor_id join public.industrial_actors b on b.id=t.buyer_actor_id where t.created_at>=now()-interval '24 hours'),
 (select count(*) from public.industrial_transports t where t.status='in_transit'),
 (select coalesce(round(avg(t.current_duration_seconds)::numeric/60,1),0)
  from public.industrial_transports t where t.created_at>=now()-interval '24 hours'),
 (select coalesce(round(100*count(*)filter(where a.actor_type='ai')/nullif(count(*),0)::numeric,1),0)
  from public.industrial_transports t join public.industrial_actors a on a.id=t.operator_actor_id where t.created_at>=now()-interval '24 hours'),
 public.industrial_world_ingot_price(),
 (select coalesce(round(100::numeric*(latest.unit_price-oldest.unit_price)/nullif(oldest.unit_price,0),1),0)
  from lateral(select s.unit_price from public.industrial_world_sales s where s.created_at>=now()-interval '24 hours' order by s.created_at desc limit 1)latest
  cross join lateral(select s.unit_price from public.industrial_world_sales s where s.created_at>=now()-interval '24 hours' order by s.created_at limit 1)oldest),
 (select count(*) from public.industrial_contracts c where c.status='open' and c.expires_at>now()),
 (select count(*) from public.industrial_actors a where a.actor_type='player'),
 (select count(*) from public.industrial_actors a join public.industrial_ai_companies c on c.id=a.ai_company_id where a.actor_type='ai' and c.enabled),
 (select count(*) from public.industrial_companies c join public.industrial_users u on u.discord_user_id=c.owner_discord_user_id
  where u.updated_at>=now()-interval '30 days'),
 (select count(*) from public.industrial_ai_companies c where c.enabled),
 (select count(*) from public.industrial_users u where u.primary_job='miner' and u.updated_at>=now()-interval '30 days')+
  (select count(*) from public.industrial_ai_companies c where c.enabled and c.job_type='miner'),
 (select count(*) from public.industrial_users u where u.primary_job='merchant' and u.updated_at>=now()-interval '30 days')+
  (select count(*) from public.industrial_ai_companies c where c.enabled and c.job_type='merchant'),
 (select count(*) from public.industrial_users u where u.primary_job='blacksmith' and u.updated_at>=now()-interval '30 days')+
  (select count(*) from public.industrial_ai_companies c where c.enabled and c.job_type='blacksmith'),
 (select count(*) from public.industrial_users u where u.primary_job='banker' and u.updated_at>=now()-interval '30 days');
$$;

revoke all on table public.industrial_resource_events from public,anon,authenticated;
revoke all on sequence public.industrial_resource_events_id_seq from public,anon,authenticated;
grant select,insert on table public.industrial_resource_events to service_role;
grant usage,select on sequence public.industrial_resource_events_id_seq to service_role;
revoke all on function public.audit_industrial_player_production(),public.audit_industrial_ai_production() from public,anon,authenticated;
revoke all on function public.get_industrial_actor_economy_stats() from public,anon,authenticated;
grant execute on function public.get_industrial_actor_economy_stats() to service_role;
