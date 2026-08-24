-- Couche d'acteurs progressive. Les colonnes Discord historiques restent compatibles.
create table public.industrial_actors(
 id bigserial primary key,actor_type text not null check(actor_type in('player','ai')),
 discord_user_id bigint references public.industrial_users(discord_user_id) on delete restrict,
 ai_company_id bigint references public.industrial_ai_companies(id) on delete restrict,
 created_at timestamptz not null default now(),updated_at timestamptz not null default now(),
 check((actor_type='player' and discord_user_id is not null and ai_company_id is null)
  or(actor_type='ai' and ai_company_id is not null and discord_user_id is null)),
 unique(discord_user_id),unique(ai_company_id));
create trigger industrial_actors_set_updated_at before update on public.industrial_actors
 for each row execute function public.set_industrial_updated_at();
alter table public.industrial_actors enable row level security;
insert into public.industrial_actors(actor_type,discord_user_id)
 select 'player',u.discord_user_id from public.industrial_users u
 on conflict on constraint industrial_actors_discord_user_id_key do nothing;
insert into public.industrial_actors(actor_type,ai_company_id)
 select 'ai',a.id from public.industrial_ai_companies a
 on conflict on constraint industrial_actors_ai_company_id_key do nothing;

create or replace function public.get_or_create_industrial_player_actor(p_discord_user_id bigint)
returns table(id bigint,actor_type text,discord_user_id bigint,ai_company_id bigint)
language plpgsql security invoker set search_path=''
as $$begin
 perform pg_catalog.pg_advisory_xact_lock(p_discord_user_id);
 insert into public.industrial_users(discord_user_id)values(p_discord_user_id)
  on conflict on constraint industrial_users_pkey do nothing;
 insert into public.industrial_actors(actor_type,discord_user_id)values('player',p_discord_user_id)
  on conflict on constraint industrial_actors_discord_user_id_key do nothing;
 return query select a.id,a.actor_type,a.discord_user_id,a.ai_company_id from public.industrial_actors a where a.discord_user_id=p_discord_user_id;
end;$$;
create or replace function public.get_industrial_ai_actor(p_ai_company_id bigint)
returns table(id bigint,actor_type text,discord_user_id bigint,ai_company_id bigint)
language plpgsql security invoker set search_path=''
as $$begin
 insert into public.industrial_actors(actor_type,ai_company_id)values('ai',p_ai_company_id)
  on conflict on constraint industrial_actors_ai_company_id_key do nothing;
 return query select a.id,a.actor_type,a.discord_user_id,a.ai_company_id from public.industrial_actors a where a.ai_company_id=p_ai_company_id;
end;$$;

alter table public.industrial_inventory add column actor_id bigint references public.industrial_actors(id) on delete restrict;
update public.industrial_inventory i set actor_id=a.id from public.industrial_actors a
 where a.discord_user_id=i.owner_discord_user_id and i.actor_id is null;
do $$begin
 if exists(select 1 from public.industrial_inventory where actor_id is null)then raise exception 'actor inventory backfill incomplete';end if;
 if exists(select 1 from public.industrial_inventory group by actor_id,resource_type having count(*)>1)then raise exception 'actor inventory duplicates';end if;
end;$$;
alter table public.industrial_inventory drop constraint industrial_inventory_pkey;
alter table public.industrial_inventory add constraint industrial_inventory_pkey unique(owner_discord_user_id,resource_type);
alter table public.industrial_inventory add constraint industrial_inventory_actor_resource_pkey primary key(actor_id,resource_type);
alter table public.industrial_inventory alter column actor_id set not null;
alter table public.industrial_inventory alter column owner_discord_user_id drop not null;

create or replace function public.enforce_industrial_inventory_actor()
returns trigger language plpgsql set search_path=''
as $$declare selected_actor public.industrial_actors%rowtype;begin
 if new.actor_id is null and new.owner_discord_user_id is not null then
  select a.* into selected_actor from public.industrial_actors a where a.discord_user_id=new.owner_discord_user_id;
  if not found then insert into public.industrial_actors(actor_type,discord_user_id)values('player',new.owner_discord_user_id)
   on conflict on constraint industrial_actors_discord_user_id_key do update
   set discord_user_id=excluded.discord_user_id returning * into selected_actor;end if;
  new.actor_id:=selected_actor.id;
 else select a.* into selected_actor from public.industrial_actors a where a.id=new.actor_id;end if;
 if selected_actor.id is null then raise exception 'invalid inventory actor' using errcode='23514';end if;
 if selected_actor.actor_type='player' then
  if new.owner_discord_user_id is null then new.owner_discord_user_id:=selected_actor.discord_user_id;end if;
  if new.owner_discord_user_id is distinct from selected_actor.discord_user_id then raise exception 'inventory actor mismatch' using errcode='23514';end if;
 elsif new.owner_discord_user_id is not null then raise exception 'AI inventory has Discord owner' using errcode='23514';end if;
 return new;end;$$;
create trigger industrial_inventory_enforce_actor before insert or update of actor_id,owner_discord_user_id
 on public.industrial_inventory for each row execute function public.enforce_industrial_inventory_actor();

alter table public.industrial_transports
 add column sender_actor_id bigint references public.industrial_actors(id) on delete restrict,
 add column receiver_actor_id bigint references public.industrial_actors(id) on delete restrict,
 add column operator_actor_id bigint references public.industrial_actors(id) on delete restrict;
update public.industrial_transports t set
 sender_actor_id=(select a.id from public.industrial_companies c join public.industrial_actors a on a.discord_user_id=c.owner_discord_user_id where c.id=t.sender_company_id),
 receiver_actor_id=(select a.id from public.industrial_companies c join public.industrial_actors a on a.discord_user_id=c.owner_discord_user_id where c.id=t.receiver_company_id),
 operator_actor_id=(select a.id from public.industrial_actors a where a.discord_user_id=t.merchant_discord_user_id)
where sender_actor_id is null or receiver_actor_id is null or operator_actor_id is null;
do $$begin if exists(select 1 from public.industrial_transports where sender_actor_id is null or receiver_actor_id is null or operator_actor_id is null)
 then raise exception 'actor transport backfill incomplete';end if;end;$$;
alter table public.industrial_transports alter column sender_actor_id set not null;
alter table public.industrial_transports alter column receiver_actor_id set not null;
alter table public.industrial_transports alter column operator_actor_id set not null;
alter table public.industrial_transports alter column sender_company_id drop not null;
alter table public.industrial_transports alter column receiver_company_id drop not null;
alter table public.industrial_transports alter column merchant_discord_user_id drop not null;

create or replace function public.enforce_industrial_transport_actors()
returns trigger language plpgsql set search_path=''
as $$declare sender public.industrial_actors%rowtype;receiver public.industrial_actors%rowtype;operator public.industrial_actors%rowtype;begin
 if new.sender_actor_id is null and new.sender_company_id is not null then select a.* into sender from public.industrial_companies c join public.industrial_actors a on a.discord_user_id=c.owner_discord_user_id where c.id=new.sender_company_id;new.sender_actor_id:=sender.id;else select a.* into sender from public.industrial_actors a where a.id=new.sender_actor_id;end if;
 if new.receiver_actor_id is null and new.receiver_company_id is not null then select a.* into receiver from public.industrial_companies c join public.industrial_actors a on a.discord_user_id=c.owner_discord_user_id where c.id=new.receiver_company_id;new.receiver_actor_id:=receiver.id;else select a.* into receiver from public.industrial_actors a where a.id=new.receiver_actor_id;end if;
 if new.operator_actor_id is null and new.merchant_discord_user_id is not null then select a.* into operator from public.industrial_actors a where a.discord_user_id=new.merchant_discord_user_id;new.operator_actor_id:=operator.id;else select a.* into operator from public.industrial_actors a where a.id=new.operator_actor_id;end if;
 if sender.id is null or receiver.id is null or operator.id is null then raise exception 'invalid transport actors' using errcode='23514';end if;
 if sender.actor_type='player' and new.sender_company_id is null then raise exception 'player sender requires company' using errcode='23514';end if;
 if receiver.actor_type='player' and new.receiver_company_id is null then raise exception 'player receiver requires company' using errcode='23514';end if;
 if operator.actor_type='player' and new.merchant_discord_user_id is distinct from operator.discord_user_id then raise exception 'transport operator mismatch' using errcode='23514';end if;
 return new;end;$$;
create trigger industrial_transports_enforce_actors before insert or update of sender_actor_id,receiver_actor_id,operator_actor_id,sender_company_id,receiver_company_id,merchant_discord_user_id
 on public.industrial_transports for each row execute function public.enforce_industrial_transport_actors();
create index industrial_transports_operator_actor_idx on public.industrial_transports(operator_actor_id,status,arrival_at);
create unique index industrial_transports_active_actor_truck_idx
 on public.industrial_transports(operator_actor_id,truck_slot) where status='in_transit';

revoke all on table public.industrial_actors from public,anon,authenticated;
revoke all on sequence public.industrial_actors_id_seq from public,anon,authenticated;
grant select,insert,update on table public.industrial_actors to service_role;
grant usage,select on sequence public.industrial_actors_id_seq to service_role;
revoke all on function public.get_or_create_industrial_player_actor(bigint),public.get_industrial_ai_actor(bigint) from public,anon,authenticated;
revoke all on function public.enforce_industrial_inventory_actor(),public.enforce_industrial_transport_actors() from public,anon,authenticated;
grant execute on function public.get_or_create_industrial_player_actor(bigint),public.get_industrial_ai_actor(bigint) to service_role;
