from flask import Flask, jsonify, request
from threading import Thread
from functools import wraps
import asyncio
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
            "highest_ranked_pts": p["highest_ranked_pts"], "highest_ranked_tier": p["highest_ranked_tier"],
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


def _r1v1_tier_label(points, tiers):
    label = tiers[0][1]
    for min_pts, name in tiers:
        if points >= min_pts:
            label = name
    return label


@app.route("/api/famille/classement_1v1")
def api_famille_classement_1v1():
    """Classement du ranked 1v1 interne au serveur (duels, !duel & co) —
    système indépendant du ranked en jeu Brawl Stars, voir ranked_1v1."""
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    entries = []
    for uid_str, p in main.ranked_1v1.items():
        member = guild.get_member(int(uid_str)) if guild else None
        if not member or member.bot:
            continue
        entries.append({
            "name": member.display_name,
            "points": p.get("points", 0),
            "wins": p.get("wins", 0),
            "losses": p.get("losses", 0),
            "tier": _r1v1_tier_label(p.get("points", 0), main.RANKED_1V1_TIERS),
        })
    entries.sort(key=lambda e: e["points"], reverse=True)
    return jsonify(entries)


@app.route("/api/famille/classement_casino")
def api_famille_classement_casino():
    """Classement de l'économie casino du bot (coins), pour la page Classement du site."""
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    entries = []
    for uid, amount in main.coins.items():
        member = guild.get_member(int(uid)) if guild else None
        if not member or member.bot:
            continue
        entries.append({"name": member.display_name, "coins": amount})
    entries.sort(key=lambda e: e["coins"], reverse=True)
    return jsonify(entries[:100])


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
    main = _bot()
    return jsonify({
        "role_ids": member["role_ids"],
        "is_admin": member["is_admin"],
        "bs_linked": discord_id in main.bs_accounts,
    })


@app.route("/api/bslink", methods=["POST"])
@_require_internal_secret
def api_bslink():
    """Liaison d'un compte Brawl Stars depuis le site (popup BsLinkModal) —
    même logique que !bslink côté Discord (_bslink_apply), juste déclenchée
    depuis Flask. Flask tourne dans un thread séparé de la boucle asyncio du
    bot ; _bslink_apply fait des appels réseau (aiohttp) et touche des
    globals partagés (bs_accounts, save_data) donc DOIT s'exécuter sur la
    boucle du bot, jamais directement ici — d'où run_coroutine_threadsafe."""
    body = request.get_json(silent=True) or {}
    discord_id = str(body.get("discord_id", "")).strip()
    tag = str(body.get("tag", "")).strip()
    if not discord_id or not tag:
        return {"error": "discord_id et tag requis"}, 400

    main = _bot()
    try:
        future = asyncio.run_coroutine_threadsafe(main._bslink_apply(discord_id, tag), main.bot.loop)
        data, err = future.result(timeout=20)
    except Exception as e:
        return {"error": f"Erreur interne : {e}"}, 500

    if err:
        return {"error": err}, 400

    return jsonify({
        "tag": data["tag"],
        "name": data["name"],
        "trophies": data["trophies"],
        "ranked_tier": data["ranked_tier"],
        "club": data.get("club"),
    })


@app.route("/api/staff/panel")
@_require_internal_secret
def api_staff_panel():
    """Panel staff : arrivées récentes (guild.members, en direct — pas besoin
    de le stocker), journal d'audit de modération (moderation_log — warn/mute/
    ban/silence/punition/morse, voir _log_moderation côté bot) et signalements
    ranked (catégorie à part, ce sont des signalements de joueurs entre eux,
    pas des actions de modérateur)."""
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    recent_members = []
    if guild:
        dated = [
            {"name": m.name, "joined_at": m.joined_at.isoformat()}
            for m in guild.members if not m.bot and m.joined_at
        ]
        recent_members = sorted(dated, key=lambda m: m["joined_at"], reverse=True)[:15]

    moderation_log = sorted(
        main.moderation_log, key=lambda e: e.get("timestamp") or "", reverse=True
    )[:30]

    reports_flat = [
        {"target": target, **r}
        for target, entries in main.ranked_reports.items()
        for r in entries
    ]
    reports_flat.sort(key=lambda r: r.get("created_at") or "", reverse=True)

    return jsonify({
        "recent_members": recent_members,
        "moderation_log": moderation_log,
        "reports": reports_flat[:20],
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
