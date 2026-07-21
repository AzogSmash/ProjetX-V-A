-- Migration du tracking Brawl Stars (bs_trophy_history, bs_family_*, bs_season_*)
-- depuis le blob JSON de main.py vers des tables normalisées.
-- À exécuter dans Supabase : Dashboard > SQL Editor > New query > coller > Run.

create table if not exists bs_family_clubs (
  tag   text primary key,
  name  text not null,
  slug  text not null unique,
  alias text
);

create table if not exists bs_players (
  tag        text primary key,
  name       text not null,
  club_tag   text references bs_family_clubs(tag) on delete set null,
  first_seen date not null default current_date
);

-- Historique quotidien des trophées — remplace bs_trophy_history[tag]['history'].
-- Une ligne par joueur par jour (contrainte d'unicité), append-only en pratique.
create table if not exists bs_trophy_snapshots (
  player_tag    text not null references bs_players(tag) on delete cascade,
  snapshot_date date not null,
  trophies      integer not null,
  primary key (player_tag, snapshot_date)
);
create index if not exists idx_trophy_snapshots_date on bs_trophy_snapshots(snapshot_date);

-- Cache ranked — remplace bs_family_ranked_cache.
create table if not exists bs_ranked_cache (
  player_tag  text primary key references bs_players(tag) on delete cascade,
  ranked_pts  integer,
  ranked_tier text,
  updated_at  timestamptz not null default now()
);

-- État de la saison en cours — une seule ligne (remplace bs_season_month/bs_season_start_date).
create table if not exists bs_season_state (
  id                 boolean primary key default true check (id),
  season_month       text,
  season_start_date  date
);
insert into bs_season_state (id) values (true) on conflict do nothing;

-- Archive des saisons passées — remplace bs_trophy_evolution_history.
create table if not exists bs_season_archive (
  season_month   text not null,
  player_tag     text not null,
  name           text not null,
  club           text,
  start_trophies integer not null,
  end_trophies   integer not null,
  delta          integer not null,
  primary key (season_month, player_tag)
);
