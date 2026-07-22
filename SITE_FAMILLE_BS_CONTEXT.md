# Contexte pour le site web "Famille Brawl Stars"

Document à copier dans le nouveau projet du site — il donne à une session Claude Code là-bas
tout le contexte nécessaire sans dépendre de cette conversation-ci.

## Projet source

- Repo `ProjetX-V-A` : bot Discord ("Vynaro"/"ProjetX"), Python, discord.py 2.7.1, persistance
  dans `data.json` (fichier JSON, pas de vraie base de données).
- Hébergé sur Railway. Un seul process : le bot Discord + un mini serveur Flask
  (`keep_alive.py`) qui tourne dans un thread daemon du même process, sur le port 8080
  (routes `/` et `/health` actuellement, juste pour garder le service actif).

## Recommandation d'architecture

Le site doit être un **projet séparé** qui consomme une petite API HTTP exposée par ce bot
(en étendant `keep_alive.py`) — pas une lecture directe de `data.json`, qui vit sur le disque
du serveur du bot et n'est pas accessible depuis un autre hébergement.

### Lire les caches déjà en mémoire, pas relancer des appels à l'API Brawl Stars

Le bot a déjà des tâches de fond qui rafraîchissent les données régulièrement :

- `sync_trophy_history` (toutes les heures) → alimente `bs_trophy_history`
- `sync_family_ranked` (toutes les 4h) → alimente `bs_family_ranked_cache`
- `check_bs_season` (vérifie toutes les 30 min, agit au 1er jeudi 10h heure de Paris) →
  alimente `bs_trophy_evolution_history`

Les routes Flask à ajouter doivent juste **sérialiser ces dicts déjà en mémoire** en JSON —
pas rappeler `_bs_fetch_club` (fonction `async`, utilise `aiohttp`) depuis une route Flask
synchrone : ça demanderait de jongler entre le thread Flask et la boucle asyncio du bot
(`asyncio.run_coroutine_threadsafe`), inutilement complexe, et ça sollicite l'API Brawl Stars
à chaque visite du site pour rien (rate limit).

## Structures de données clés (variables globales de `main.py`, aussi dans `data.json`)

### `bs_family_clubs` — les clans configurés (actuellement vidés, à re-remplir via `!bs_famille ajouter`)

```json
[{"tag": "ABC123", "name": "ProjetX", "slug": "projetx", "alias": "px"}]
```

### `bs_trophy_history` — suivi quotidien des trophées, par tag joueur Brawl Stars

```json
{
  "PLAYER_TAG": {
    "name": "Alice",
    "club": "ProjetX",
    "first_seen": "2026-07-02",
    "history": [
      {"date": "2026-07-02", "trophies": 500},
      {"date": "2026-07-03", "trophies": 520}
    ]
  }
}
```

- Une entrée par jour maximum (mise à jour en place si plusieurs synchros le même jour).
- Rétention 90 jours (`BS_HISTORY_RETENTION_DAYS`).
- `history[-1]['trophies']` = dernier point connu = "trophées actuels" pour un affichage web.

### `bs_season_month` / `bs_season_start_date` — saison Brawl Stars en cours

- `bs_season_month` : `"YYYY-MM"` du mois où la saison en cours a démarré.
- `bs_season_start_date` : `"YYYY-MM-DD"` — date exacte du 1er jeudi 10h (heure de Paris) qui a
  lancé la saison en cours. Sert de borne pour calculer l'évolution de la saison en cours
  (trophées actuels − trophées à cette date).

### `bs_trophy_evolution_history` — saisons archivées (remplie au changement de saison)

```json
{
  "2026-07": {
    "PLAYER_TAG": {"name": "Alice", "club": "ProjetX", "start": 500, "end": 700, "delta": 200}
  }
}
```

### `bs_family_ranked_cache` — points classés, rafraîchi toutes les 4h

```json
{"PLAYER_TAG": {"name": "Alice", "club": "ProjetX", "ranked_pts": 1200, "ranked_tier": "Or II"}}
```

`bs_family_ranked_updated_at` : string affichable `"JJ/MM HH:MM"` de la dernière synchro.

### `bs_accounts` — liaisons Discord ↔ Brawl Stars (actuellement vide, perdu lors de l'incident de perte de données)

```json
{"discord_uid_str": {"tag": "...", "name": "...", "trophies": 0, "ranked_pts": null, "ranked_tier": null}}
```

À reconstruire au fur et à mesure que les membres refont `!bslink`. Le site ne doit **pas**
dépendre de ce lien pour afficher les stats de clan — tout le reste fonctionne par tag Brawl
Stars, indépendamment de Discord.

## Endpoints à ajouter à `keep_alive.py` (proposition)

Toutes en `GET`, lecture seule, données publiques Brawl Stars — pas d'auth nécessaire a priori.

- `GET /api/famille/clans` → liste des clans (`bs_family_clubs`).
- `GET /api/famille/trophees` → pour chaque tag de `bs_trophy_history`, dernier point connu
  (`history[-1]`) + club/nom. Équivalent web de `!classement_trophees_famille`.
- `GET /api/famille/ranked` → contenu de `bs_family_ranked_cache` + `bs_family_ranked_updated_at`.
- `GET /api/famille/evolution` → évolution de la saison en cours : pour chaque tag, delta =
  dernier point − premier point ≥ `bs_season_start_date`.
- `GET /api/famille/evolution/<mois>` (ex. `/api/famille/evolution/2026-07`) → lit directement
  `bs_trophy_evolution_history[mois]`.
- `GET /api/famille/saisons` → liste des clés de `bs_trophy_evolution_history` (pour peupler un
  sélecteur de saison côté site, comme `!evo` sur Discord).

## Notes d'implémentation

- Ces dicts vivent dans le process du bot (variables globales de `main.py`) ; le thread Flask
  de `keep_alive.py` tourne dans le **même process**, donc il peut les importer et les lire
  directement (`import main` puis `main.bs_trophy_history`) — pas besoin de fichier
  intermédiaire.
- Le GIL Python rend la lecture simple de ces dicts sûre depuis un autre thread, mais éviter
  d'appeler des fonctions `async def` du bot (ex. `_bs_fetch_club`) directement depuis Flask.
- CORS : si le site fait ses appels API depuis le navigateur (JS côté client), il faudra
  activer CORS sur ces routes Flask (`flask-cors` ou headers manuels). Si le site fait ses
  appels côté serveur (SSR), pas besoin.
- URL publique actuelle du keep-alive : dépend du domaine Railway attribué au service (à
  vérifier dans le dashboard Railway).

## Autres systèmes Discord pertinents (si le site veut aussi les afficher un jour)

- `!classement_1v1` / `ranked_1v1` / `ranked_1v1_history` : classement PVP interne au serveur,
  sans rapport avec Brawl Stars, système indépendant.
- Reset casino automatique mensuel (`check_casino_season`) : sans rapport avec les saisons
  Brawl Stars, ne pas confondre les deux cycles.
