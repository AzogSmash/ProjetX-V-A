-- Index de palier officiel (highestAllTimeRankedRank côté API Brawl Stars),
-- en plus du score (highest_ranked_pts) et du nom (highest_ranked_tier) déjà
-- suivis. Sert de clé de tri fiable pour le classement "Ranked all-time" :
-- le score brut n'est pas forcément comparable d'un joueur à l'autre quand
-- le record a été fait sous l'ancien système Ranked (voir TIER_LOGIC_BRIEF.md),
-- alors que cet index de palier reste cohérent.
alter table bs_ranked_cache add column if not exists highest_ranked_rank integer;
