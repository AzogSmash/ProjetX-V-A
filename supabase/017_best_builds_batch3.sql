-- Troisième lot de builds fournis par le staff (17/08/2026) — voir
-- 012_best_builds.sql pour le contexte de la table.
insert into best_builds (brawler_slug, brawler_name, comment) values
  ('arkad', 'Arkad', 'Arkad fait d''ores et déjà beaucoup de dégâts et possède énormément de vie, ce qui lui permet de profiter pleinement de ces deux équipements. Si vous devez jouer en tant que porteur de gemmes en Razzia, vous pouvez jouer l''autre pouvoir star et l''autre gadget pour rester en vie plus facilement (accompagné d''un gear bouclier). L''équipement vitesse est très intéressant sur certaines maps. Astuce supplémentaire : si vous vous retrouvez face à Colette, prenez l''équipement bouclier.')
on conflict (brawler_slug) do nothing;
