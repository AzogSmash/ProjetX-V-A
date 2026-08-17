-- Déclarations d'absence des membres (club, dates, raison, événement
-- manqué) — postées via le panel !absence_panel (select club + modal, voir
-- main.py) ou consultées depuis le site (panel staff, voir
-- GET /api/admin/absences dans keep_alive.py). Suppression en dur (pas de
-- soft-delete) : la déclaration disparaît de la table dès qu'elle est
-- retirée, par son auteur ou par un admin (voir _delete_absence_apply).
create table if not exists absences (
  id bigint generated always as identity primary key,
  discord_id text not null,
  club text not null,
  start_date date not null,
  return_date date,
  missed_event text,
  reason text not null,
  created_at timestamptz not null default now()
);

-- Tri/filtre principal côté staff (!absences [club], /api/admin/absences).
create index if not exists absences_club_idx on absences (club);
create index if not exists absences_discord_id_idx on absences (discord_id);

-- Aucune policy nécessaire : seul le bot accède à cette table, via
-- SUPABASE_SERVICE_ROLE_KEY, qui ignore toujours RLS par conception
-- Supabase (voir 008_enable_rls.sql — sans ça, cette table réintroduirait
-- exactement la faille de sécurité qu'on vient de corriger).
alter table absences enable row level security;
