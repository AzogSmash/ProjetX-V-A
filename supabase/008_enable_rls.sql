-- Sécurité critique : aucune table n'avait Row Level Security activé,
-- rendant TOUTES les données lisibles (et probablement modifiables) par
-- n'importe qui via la clé anon publique du site, en contournant
-- complètement le bot et INTERNAL_API_SECRET (trouvé le 23/07/2026, avant
-- ouverture publique du site).
--
-- Aucune policy nécessaire : seul le bot accède à ces tables, via
-- SUPABASE_SERVICE_ROLE_KEY, qui ignore toujours RLS par conception
-- Supabase. Activer RLS sans policy ferme donc l'accès public (anon /
-- authenticated) sans rien changer côté bot.
alter table bs_family_clubs enable row level security;
alter table bs_players enable row level security;
alter table bs_trophy_snapshots enable row level security;
alter table bs_ranked_cache enable row level security;
alter table bs_season_state enable row level security;
alter table bs_season_archive enable row level security;
alter table discord_members enable row level security;
alter table bs_player_profiles enable row level security;
alter table bs_news enable row level security;
