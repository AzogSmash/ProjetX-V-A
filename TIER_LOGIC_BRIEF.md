# Fix à faire : tier "record" faux pour les comptes ayant joué sous l'ancien système Ranked

## Contexte

Brawl Stars a changé de système Ranked. L'ancien système exposait le rang via un **nom**
(`"Legendary III"`, etc.), le nouveau l'expose via un **score numérique** de trophées.
Le champ `highest_ranked_pts` / `highest_ranked_tier` de ce projet (record all-time d'un
joueur) est actuellement calculé **uniquement à partir du score numérique**, ce qui donne
un tier faux pour les joueurs dont le record a été fait sous l'ancien système (score
souvent 0/absent, alors qu'un nom de rang existe).

## Où c'est cassé

Fichier `main.py`, fonction `_bs_fetch_ranked_pts` (~ligne 10152) :

```python
async def _bs_fetch_ranked_pts(session: aiohttp.ClientSession, clean_tag: str):
    ...
    async with session.get(f"{RNT_API_BASE}?tag={clean_tag}", ...) as rnt_resp:
        if rnt_resp.status == 200:
            rnt_data = await rnt_resp.json(content_type=None)
            stats = (rnt_data.get('result') or {}).get('stats', [])
            val = next((s.get('value') for s in stats if s.get('id') == 24), None)
            highest_val = next((s.get('value') for s in stats if s.get('id') == 25), None)
            return (
                val, _ranked_tier_name(val) if val is not None else None,
                highest_val, _ranked_tier_name(highest_val) if highest_val is not None else None,
            )
```

Elle ne lit que `stats` (id 24 = current pts, id 25 = highest pts) et calcule toujours
le tier via `_ranked_tier_name(points)` (table par score, ~ligne 10095, `RANKED_TIERS`).
Elle ignore complètement le nom de rang que l'API renvoie pour l'ancien système.

## Règle à appliquer

Pour savoir sous quel système un record a été fait, **ne pas se baser sur une date**,
mais sur la présence du champ `highestAllTimeRankedRankName` renvoyé par l'API Brawl Stars
(présent dans `rnt_data['result']`, au même niveau que `stats`, pas dedans) :

- S'il est rempli (ex: `"Legendary III"`) → le record a été fait sous l'**ancien** système
  → mapper le tier **par nom**.
- S'il est vide/absent → le record a été fait sous le **nouveau** système
  → mapper le tier **par score** (`highest_ranked_pts`, logique déjà en place via
  `_ranked_tier_name`).

Le rang "current" (`ranked_pts`/`ranked_tier`) n'a pas besoin de ce fallback : l'ancien
système est terminé, donc le rang courant est toujours sous le nouveau système. Seul le
"highest / record" peut dater de l'ancien système.

## Ce qu'il manque pour l'implémenter

1. Table de mapping nom → tier (équivalent nommage du projet, à dériver de `RANKED_TIERS`) :

```python
RANKED_TIER_NAMES_OLD_SYSTEM = {
    "bronze i": "Bronze 1", "bronze ii": "Bronze 2", "bronze iii": "Bronze 3",
    "silver i": "Argent 1", "silver ii": "Argent 2", "silver iii": "Argent 3",
    "gold i": "Or 1", "gold ii": "Or 2", "gold iii": "Or 3",
    "diamond i": "Diamant 1", "diamond ii": "Diamant 2", "diamond iii": "Diamant 3",
    "mythic i": "Mythique 1", "mythic ii": "Mythique 2", "mythic iii": "Mythique 3",
    "legendary i": "Légendaire 1", "legendary ii": "Légendaire 2", "legendary iii": "Légendaire 3",
    "masters i": "Masters 1", "masters ii": "Masters 2", "masters iii": "Masters 3",
    "pro": "Pro",
}

def _ranked_tier_name_from_old_rank_name(name: str) -> str | None:
    return RANKED_TIER_NAMES_OLD_SYSTEM.get(name.strip().lower())
```

(vérifier le nommage exact renvoyé par l'API réelle — l'API BS peut renvoyer les noms
en anglais avec chiffres romains, à confirmer avec un vrai payload avant de coder en dur.)

2. Dans `_bs_fetch_ranked_pts`, récupérer aussi `rnt_data['result'].get('highestAllTimeRankedRankName')`
   et l'utiliser en priorité pour calculer `highest_tier` :

```python
result = rnt_data.get('result') or {}
stats = result.get('stats', [])
val = next((s.get('value') for s in stats if s.get('id') == 24), None)
highest_val = next((s.get('value') for s in stats if s.get('id') == 25), None)
highest_rank_name = result.get('highestAllTimeRankedRankName') or None

if highest_rank_name:
    highest_tier = _ranked_tier_name_from_old_rank_name(highest_rank_name)
else:
    highest_tier = _ranked_tier_name(highest_val) if highest_val is not None else None

return (
    val, _ranked_tier_name(val) if val is not None else None,
    highest_val, highest_tier,
)
```

## À vérifier avant de coder

- Confirmer que `api.rnt.dev` (source tierce utilisée ici, pas l'API officielle) renvoie
  bien `highestAllTimeRankedRankName` dans son payload `result` — logguer une réponse
  brute pour un joueur qui a un vieux record et checker la clé exacte.
- Le tableau `tier_counts` / classement (fonction autour de la ligne 11191, qui compte les
  membres par `m['ranked_tier']`) fonctionne déjà par nom de string donc pas de changement
  nécessaire côté agrégation, tant que `highest_ranked_tier` contient un nom cohérent avec
  `RANKED_TIERS`.
