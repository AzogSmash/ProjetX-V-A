-- Anti-spam pour _prompt_bs_tag_onboarding (main.py) : un membre ne doit
-- recevoir le MP "Dernière étape : ton tag Brawl Stars" qu'une fois par
-- fenêtre de 24h, même si le déclencheur (before.pending -> after.pending
-- côté on_member_update) se déclenche plusieurs fois à tort — constaté le
-- 17/08/2026, probablement à cause d'un état de cache pas encore chaud
-- juste après un redémarrage du bot (on a redéployé énormément aujourd'hui),
-- qui faussait la comparaison before/after sur un simple changement de rôle.
create table if not exists bs_tag_onboarding_prompts (
  discord_id text primary key,
  last_prompted_at timestamptz not null default now()
);

alter table bs_tag_onboarding_prompts enable row level security;
