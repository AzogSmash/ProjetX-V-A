-- Record all-time de points classés (HighestRankedPoints, stat id 25 côté
-- api.rnt.dev) en plus du CurrentRankedPoints déjà suivi (id 24) — pour
-- l'onglet "Ranked all-time" du site.
alter table bs_ranked_cache add column if not exists highest_ranked_pts integer;
alter table bs_ranked_cache add column if not exists highest_ranked_tier text;
