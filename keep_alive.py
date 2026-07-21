from flask import Flask, jsonify
from threading import Thread
import logging
import sys

app = Flask("")

# Désactive les logs Flask pour réduire le spam
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


@app.route("/")
def home():
    return "🤖 HappyBot is online and running!"


@app.route("/health")
def health():
    return {"status": "healthy", "bot": "active"}, 200


# ── API famille Brawl Stars ──
# Lecture seule des dicts déjà en mémoire dans main.py (alimentés par les
# tâches de fond du bot). main.py est lancé en `python main.py`, donc il vit
# dans sys.modules sous le nom "__main__" — PAS "main". Un `import main`
# ici déclencherait un tout nouvel import (main.py n'a jamais été chargé
# sous ce nom), qui ré-exécute tout le fichier depuis une autre thread
# jusqu'à un second bot.run(token) qui bloque indéfiniment — deadlock,
# et pire, une deuxième connexion Discord. On récupère donc directement
# le module déjà en cours d'exécution via sys.modules.
# Voir SITE_FAMILLE_BS_CONTEXT.md pour le détail des structures.

def _bot():
    return sys.modules["__main__"]


@app.route("/api/famille/clans")
def api_famille_clans():
    main = _bot()
    return jsonify(main.bs_family_clubs)


@app.route("/api/famille/trophees")
def api_famille_trophees():
    main = _bot()
    players = []
    for tag, data in main.bs_trophy_history.items():
        history = data.get("history") or []
        if not history:
            continue
        last = history[-1]
        players.append({
            "tag": tag,
            "name": data.get("name"),
            "club": data.get("club"),
            "trophies": last.get("trophies"),
            "date": last.get("date"),
        })
    players.sort(key=lambda p: p["trophies"] or 0, reverse=True)
    return jsonify(players)


@app.route("/api/famille/ranked")
def api_famille_ranked():
    main = _bot()
    return jsonify({
        "players": main.bs_family_ranked_cache,
        "updated_at": main.bs_family_ranked_updated_at,
    })


@app.route("/api/famille/evolution")
def api_famille_evolution():
    main = _bot()
    start_date = main.bs_season_start_date
    players = []
    for tag, data in main.bs_trophy_history.items():
        history = data.get("history") or []
        if not history:
            continue
        last = history[-1]
        eligible = [h for h in history if start_date and h.get("date") and h["date"] >= start_date]
        first = eligible[0] if eligible else history[0]
        delta = (last.get("trophies") or 0) - (first.get("trophies") or 0)
        players.append({
            "tag": tag,
            "name": data.get("name"),
            "club": data.get("club"),
            "start": first.get("trophies"),
            "end": last.get("trophies"),
            "delta": delta,
        })
    players.sort(key=lambda p: p["delta"], reverse=True)
    return jsonify({
        "season_month": main.bs_season_month,
        "season_start_date": start_date,
        "players": players,
    })


@app.route("/api/famille/evolution/<mois>")
def api_famille_evolution_mois(mois):
    main = _bot()
    data = main.bs_trophy_evolution_history.get(mois)
    if data is None:
        return {"error": "saison introuvable"}, 404
    return jsonify(data)


@app.route("/api/famille/saisons")
def api_famille_saisons():
    main = _bot()
    return jsonify(sorted(main.bs_trophy_evolution_history.keys()))


@app.route("/api/famille/clan/<tag>")
def api_famille_clan_detail(tag):
    main = _bot()
    clean = tag.strip().lstrip("#").upper()
    data = main.bs_family_club_details.get(clean)
    if data is None:
        return {"error": "club introuvable ou pas encore synchronisé"}, 404
    return jsonify(data)


@app.route("/api/famille/_export_pour_migration")
def api_famille_export_temporaire():
    # Route TEMPORAIRE pour récupérer l'état actuel avant bascule vers
    # Supabase (voir migration du tracking BS) — à supprimer juste après.
    main = _bot()
    return jsonify({
        "bs_family_clubs": main.bs_family_clubs,
        "bs_trophy_history": main.bs_trophy_history,
        "bs_family_ranked_cache": main.bs_family_ranked_cache,
        "bs_family_ranked_updated_at": main.bs_family_ranked_updated_at,
        "bs_season_month": main.bs_season_month,
        "bs_season_start_date": main.bs_season_start_date,
        "bs_trophy_evolution_history": main.bs_trophy_evolution_history,
    })


def run():
    try:
        app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
    except Exception as e:
        print(f"Erreur serveur web : {e}")


def keep_alive():
    t = Thread(target=run)
    t.daemon = True  # Le thread se ferme avec le programme principal
    t.start()
    print("🌐 Serveur web Keep-Alive démarré sur le port 8080")
