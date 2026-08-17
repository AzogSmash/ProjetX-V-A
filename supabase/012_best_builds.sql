-- "Meilleurs builds" : un build recommandé par brawler (gadget, équipements,
-- pouvoir star, hypercharge), avec un commentaire expliquant le choix et ses
-- adaptations selon les maps/modes. L'image du loadout est un fichier
-- statique côté site (public/brawler-builds/{brawler_slug}.png), pas stockée
-- ici — voir SITE_FAMILLE_BS_CONTEXT.md pour la philosophie "bot = passerelle
-- de données, site = rendu".
--
-- Un seul build par brawler (demande du 17/08/2026) : brawler_slug est la
-- clé primaire, un nouvel ajout pour le même brawler remplace l'ancien
-- (upsert, voir db_bs.upsert_best_build).
create table if not exists best_builds (
  brawler_slug text primary key,
  brawler_name text not null,
  comment text not null,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Aucune policy nécessaire : seul le bot accède à cette table, via
-- SUPABASE_SERVICE_ROLE_KEY, qui ignore toujours RLS par conception
-- Supabase (voir 008_enable_rls.sql).
alter table best_builds enable row level security;

-- Premiers builds fournis par le staff (17/08/2026), reformatés pour la
-- lisibilité — une ligne par brawler. brawler_slug doit correspondre au nom
-- de fichier dans public/brawler-builds/ côté site (kebab-case).
insert into best_builds (brawler_slug, brawler_name, comment) values
  ('shelly', 'Shelly', 'Shelly se joue principalement sur les maps avec des murs et des buissons, on prend ces équipements pour accentuer ses forces (gros dégâts et avantage dans les buissons). S''il n''y a que peu de buissons, je vous conseille de mettre l''équipement bouclier au lieu de vitesse.'),
  ('nita', 'Nita', 'Nita est très forte contre les tanks mais a une portée assez faible. On utilise le gear vitesse pour pallier ce manque de portée. Vous pouvez changer de pouvoir star dans le mode Braquage, mais l''objectif reste de garder l''ours le plus longtemps en vie pour embêter l''équipe adverse dans tous les autres modes.'),
  ('colt', 'Colt', 'Colt a pour but premier d''ouvrir les murs (ou les buissons) le plus vite possible et de permettre à votre équipe de jouer l''objectif plus facilement. Vous pouvez alterner entre ces trois équipements. Vous pouvez aussi changer de gadget, les deux sont très bons. L''autre pouvoir star se joue uniquement sur certaines maps de Braquage, pour atteindre le coffre plus facilement.'),
  ('bull', 'Bull', 'Bull fait déjà très mal au corps à corps, et a envie d''y rester longtemps — on joue ce build dans ce but. Sur une map avec beaucoup de buissons, vous pouvez tout à fait jouer de manière plus agressive en mettant l''équipement dégâts et vitesse.'),
  ('brock', 'Brock', 'Tout comme Colt, l''objectif premier de Brock est de casser la map le plus rapidement possible pour pouvoir jouer à distance, où il excelle. Le saut n''est pas un mauvais gadget, mais vous perdez énormément en polyvalence : je vous conseille de le jouer uniquement en cas d''assassins adverses, quand vous n''avez pas besoin de casser quoi que ce soit.'),
  ('el-primo', 'El Primo', 'El Primo n''est utilisable que sur les maps avec des murs et des buissons à foison. Les deux pouvoirs star sont jouables. L''objectif de ce build est de charger et cycler les ultimes du personnage, son seul grand atout.'),
  ('bartaba', 'Bartaba', 'Bartaba est excellent dans les modes de contrôle et sur les maps possédant des murs hauts. Grâce à son gadget, il permet à ses alliés de garder une position encore plus longtemps, et il est très difficile à éliminer avec son pouvoir star. L''autre gadget est excellent contre les assassins quand Bartaba est collé à un mur. Si vous le jouez offensivement en Braquage, n''oubliez pas de changer de pouvoir star.')
on conflict (brawler_slug) do nothing;
