-- Miroir des membres Discord (ID + rôles + permission admin) pour piloter les
-- 4 niveaux d'accès du site (invité / membre de clan / staff / admin), sans
-- dépendre d'un scope OAuth supplémentaire ni d'un token qui expire. Alimenté
-- par une tâche de fond du bot (source de vérité = l'état réel du serveur
-- Discord) ; lu par le site via une route Flask dédiée. La signification des
-- role_ids (quel rôle = quel club / staff / admin) vit côté site, pas ici —
-- cette table ne fait que refléter l'état brut du serveur.
create table if not exists discord_members (
  discord_id text primary key,
  username    text,
  role_ids    text[] not null default '{}',
  is_admin    boolean not null default false,
  synced_at   timestamptz not null default now()
);
create index if not exists idx_discord_members_role_ids on discord_members using gin (role_ids);
