-- Quatrième lot de builds fournis par le staff (17/08/2026) — voir
-- 012_best_builds.sql pour le contexte de la table. Jacky manque encore
-- (image pas reçue), sera ajouté dans un lot suivant.
insert into best_builds (brawler_slug, brawler_name, comment) values
  ('ricochet', 'Ricochet', 'Les deux gadgets de Ricochet sont très bons, bien que l''autre soit plus simple à jouer, la boîboîte est plus versatile et complète le kit du personnage, on va la favoriser. Elle synergise parfaitement avec le pouvoir star, qui vous permettra d''infliger encore plus de dégâts. Vous allez privilégier l''autre pouvoir star dans les maps très ouvertes de Hors-jeu et Prime.'),
  ('darryl', 'Darryl', 'Ce build de Darryl est le meilleur dans tous les modes hors Braquage. Jouez l''autre gadget et pouvoir star en Braquage.'),
  ('penny', 'Penny', 'Contrairement à Jessie, Penny est une vraie menace même sans son mortier. Grâce à son gadget qui complète parfaitement son kit, elle peut détruire des assassins facilement et garder sa position — il permet aussi de tanker une balle d''un sniper. Le pouvoir star permet de toucher les cibles de l''arrière-garde encore plus facilement. Le gear vision est très efficace grâce à sa longue portée et ses excellents dégâts de zone, à jouer sur les maps possédant beaucoup de buissons. L''autre pouvoir star est rarement utile, mais il peut être joué pour contrer des menaces comme Shade.'),
  ('carl', 'Carl', 'Peu de choses à dire sur Carl, c''est un brawler solide. Le gadget du feu vous donne plus de contrôle, tandis que le second vous donne plus de mobilité (l''autre pouvoir star handicape le personnage, évitez de le jouer). Il est aussi un bon utilisateur du gear vision quand nécessaire.'),
  ('gus', 'Gus', 'Tout comme Carl, Gus est un personnage très solide : il est un excellent utilisateur du gear vision grâce à son attaque épaisse et sa grande portée.')
on conflict (brawler_slug) do nothing;
