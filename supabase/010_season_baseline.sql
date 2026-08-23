-- Valeur de trophées de chaque joueur figée au moment exact du reset de
-- saison (détecté par check_bs_season, 1er jeudi du mois à 10h heure de
-- Paris) — jamais écrasée ensuite, contrairement à bs_trophy_snapshots (une
-- seule ligne par jour, réécrite à chaque sync horaire). Sans cette table,
-- la "valeur de départ" du push de saison dépendait du premier sync
-- quotidien, qui pouvait écraser plusieurs fois la vraie valeur de 10h avant
-- que quiconque ne la consulte (incident du 07/08/2026 : delta à 0 pour tout
-- le monde pendant ~14h après le reset d'août).
create table if not exists bs_season_baseline (
  season_month text not null,
  player_tag text not null,
  trophies integer not null,
  captured_at timestamptz not null default now(),
  primary key (season_month, player_tag)
);

alter table bs_season_baseline enable row level security;
