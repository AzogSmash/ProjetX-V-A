-- Système de tickets maison, remplace le bot tiers "tickets.bot". Ouvrable
-- depuis Discord (panel + modal) et depuis le site (formulaire réservé aux
-- comptes liés). transcript stocké au moment de la fermeture, consultable
-- via le site (/staff/tickets/[id]) — voir le lien posté dans #logs-ticket.
create table if not exists tickets (
  id bigint generated always as identity primary key,
  discord_id text not null,
  bs_tag text,
  category text not null check (category in ('candidature', 'club_recruitment', 'incident', 'other')),
  description text not null,
  channel_id text not null,
  status text not null default 'open' check (status in ('open', 'closed')),
  claimed_by text,
  closed_by text,
  close_reason text,
  transcript jsonb,
  created_at timestamptz not null default now(),
  closed_at timestamptz
);

-- Un seul ticket ouvert par utilisateur (anti-spam) : lookup fréquent dans
-- get_open_ticket_for_user, direction opposée à la fermeture par channel_id.
create index if not exists tickets_open_by_user_idx on tickets (discord_id) where status = 'open';

-- Aucune policy nécessaire : seul le bot accède à cette table, via
-- SUPABASE_SERVICE_ROLE_KEY, qui ignore toujours RLS par conception
-- Supabase (voir 008_enable_rls.sql — sans ça, cette table réintroduirait
-- exactement la faille de sécurité qu'on vient de corriger).
alter table tickets enable row level security;
