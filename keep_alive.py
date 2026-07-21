from flask import Flask, jsonify, request
from threading import Thread
from functools import wraps
import logging
import os
import secrets
import sys

import db_bs
import db_members

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
# Le tracking BS (clans, historique de trophées, cache ranked, saisons) vit
# dans Supabase (voir db_bs.py) — ces routes lisent donc directement la base,
# sans dépendre de l'état en mémoire du process bot. Seule /api/famille/clan/<tag>
# reste sur sys.modules["__main__"] : elle sert bs_family_club_details, un cache
# volontairement PAS persisté (voir commentaire dans main.py), reconstruit à
# chaque sync_trophy_history depuis l'API officielle Brawl Stars.

def _bot():
    return sys.modules["__main__"]


@app.route("/api/famille/clans")
def api_famille_clans():
    return jsonify(db_bs.list_family_clubs())


@app.route("/api/famille/trophees")
def api_famille_trophees():
    players = db_bs.get_latest_trophies()
    players.sort(key=lambda p: p["trophies"] or 0, reverse=True)
    return jsonify(players)


@app.route("/api/famille/ranked")
def api_famille_ranked():
    players = {
        p["tag"]: {
            "name": p["name"], "club": p["club"],
            "ranked_pts": p["ranked_pts"], "ranked_tier": p["ranked_tier"],
        }
        for p in db_bs.get_ranked_cache()
    }
    return jsonify({
        "players": players,
        "updated_at": db_bs.get_ranked_updated_at(),
    })


@app.route("/api/famille/evolution")
def api_famille_evolution():
    state = db_bs.get_season_state()
    start_date = state["season_start_date"]
    players = db_bs.get_season_evolution(start_date) if start_date else []
    players.sort(key=lambda p: p["delta"], reverse=True)
    return jsonify({
        "season_month": state["season_month"],
        "season_start_date": start_date,
        "players": players,
    })


@app.route("/api/famille/evolution/<mois>")
def api_famille_evolution_mois(mois):
    entries = db_bs.get_archived_season(mois)
    if not entries:
        return {"error": "saison introuvable"}, 404
    data = {
        e["tag"]: {"name": e["name"], "club": e["club"], "start": e["start"], "end": e["end"], "delta": e["delta"]}
        for e in entries
    }
    return jsonify(data)


@app.route("/api/famille/saisons")
def api_famille_saisons():
    return jsonify(db_bs.list_archived_seasons())


@app.route("/api/famille/clan/<tag>")
def api_famille_clan_detail(tag):
    main = _bot()
    clean = tag.strip().lstrip("#").upper()
    data = main.bs_family_club_details.get(clean)
    if data is None:
        return {"error": "club introuvable ou pas encore synchronisé"}, 404
    return jsonify(data)


# ── Niveaux d'accès du site (voir supabase/003_discord_members.sql) ──
# Contrairement au reste de l'API famille (public, données de classement),
# is_admin/role_ids sert à décider qui voit le panel staff/admin du site —
# donc protégé par un secret partagé site<->bot plutôt que laissé ouvert.

def _require_internal_secret(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = os.environ.get("INTERNAL_API_SECRET")
        got = request.headers.get("X-Internal-Secret", "")
        if not expected or not secrets.compare_digest(got, expected):
            return {"error": "unauthorized"}, 401
        return fn(*args, **kwargs)
    return wrapper


@app.route("/api/member/<discord_id>")
@_require_internal_secret
def api_member(discord_id):
    member = db_members.get_member(discord_id)
    if member is None:
        return {"error": "pas membre du serveur"}, 404
    return jsonify({"role_ids": member["role_ids"], "is_admin": member["is_admin"]})


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
