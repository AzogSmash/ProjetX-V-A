-- État de la relance hebdomadaire du tag Brawl Stars (remind_bs_tag_missing,
-- main.py) — une seule ligne, même technique que bs_season_state
-- (001_bs_tracking.sql). Sans ça, la tâche périodique repart de zéro à
-- CHAQUE redémarrage du bot, puisque tasks.loop exécute son corps
-- immédiatement au premier .start() — incident du 17/08/2026 (3 relances en
-- MP le même jour, un redéploiement Railway par push).
create table if not exists bs_tag_reminder_state (
  id boolean primary key default true check (id),
  last_sent_at timestamptz
);
insert into bs_tag_reminder_state (id) values (true) on conflict do nothing;

alter table bs_tag_reminder_state enable row level security;
