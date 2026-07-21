from flask import Flask, jsonify
from threading import Thread
import logging

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
# tâches de fond du bot). Import différé pour éviter tout souci d'import
# circulaire (main.py importe ce module au chargement) et parce que ces
# routes ne sont de toute façon appelées qu'une fois le bot démarré.
# Voir SITE_FAMILLE_BS_CONTEXT.md pour le détail des structures.

@app.route("/api/famille/clans")
def api_famille_clans():
    import main
    return jsonify(main.bs_family_clubs)


@app.route("/api/famille/trophees")
def api_famille_trophees():
    import main
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
    import main
    return jsonify({
        "players": main.bs_family_ranked_cache,
        "updated_at": main.bs_family_ranked_updated_at,
    })


@app.route("/api/famille/evolution")
def api_famille_evolution():
    import main
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
    import main
    data = main.bs_trophy_evolution_history.get(mois)
    if data is None:
        return {"error": "saison introuvable"}, 404
    return jsonify(data)


@app.route("/api/famille/saisons")
def api_famille_saisons():
    import main
    return jsonify(sorted(main.bs_trophy_evolution_history.keys()))


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
