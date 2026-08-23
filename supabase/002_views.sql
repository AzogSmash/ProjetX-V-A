-- Vue pratique : dernier point de trophées connu par joueur, avec le nom du
-- club déjà résolu — évite de faire un DISTINCT ON à la main côté Python à
-- chaque requête. Équivalent de `bs_trophy_history[tag]['history'][-1]`.
create or replace view bs_latest_trophies as
select distinct on (s.player_tag)
  s.player_tag as tag,
  p.name,
  c.name as club,
  s.trophies,
  s.snapshot_date as date
from bs_trophy_snapshots s
join bs_players p on p.tag = s.player_tag
left join bs_family_clubs c on c.tag = p.club_tag
order by s.player_tag, s.snapshot_date desc;
