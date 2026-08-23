-- Profil personnalisable par joueur (bio + screenshot du profil in-game),
-- éditable uniquement par le joueur dont le compte Discord est lié à ce tag
-- (voir bs_accounts côté bot). Une ligne n'existe que si le joueur a
-- personnalisé quelque chose — absence de ligne = profil par défaut (juste
-- les stats synchronisées, pas de bio/screenshot).
create table if not exists bs_player_profiles (
  player_tag text primary key,
  bio text,
  screenshot_url text,
  updated_at timestamptz not null default now()
);
