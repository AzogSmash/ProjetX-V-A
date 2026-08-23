-- Actualités du site, publiées par le staff/admin depuis leur panel
-- (remplace les actus codées en dur côté site). icon limité aux 4 valeurs
-- gérées par NewsCard côté site.
create table if not exists bs_news (
  id bigint generated always as identity primary key,
  icon text not null check (icon in ('skull', 'shield', 'message', 'trophy')),
  title text not null,
  description text not null,
  author text,
  created_at timestamptz not null default now()
);
