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
            "highest_ranked_rank": p.get("highest_ranked_rank"),
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
    players = db_bs.get_season_evolution(start_date, state["season_month"]) if start_date else []
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


@app.route("/api/famille/recherche")
def api_famille_recherche():
    """Recherche joueurs + clubs pour la barre de recherche du site — public,
    mêmes données que /api/famille/trophees et /api/famille/clans, déjà
    visibles sur les classements."""
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"players": [], "clubs": []})

    players = [
        {"tag": p["tag"], "name": p["name"], "club": p["club"]}
        for p in db_bs.get_latest_trophies()
        if p.get("name") and q in p["name"].lower()
    ][:8]

    clubs = [
        {"slug": c["slug"], "name": c["name"]}
        for c in db_bs.list_family_clubs()
        if q in c["name"].lower()
    ][:8]

    return jsonify({"players": players, "clubs": clubs})


@app.route("/api/famille/actualites")
def api_famille_actualites():
    limit = request.args.get("limit", type=int)
    return jsonify(db_bs.list_news(limit))


def _r1v1_tier_label(points, tiers):
    label = tiers[0][1]
    for min_pts, name in tiers:
        if points >= min_pts:
            label = name
    return label


@app.route("/api/famille/classement_1v1")
def api_famille_classement_1v1():
    """Classement du ranked 1v1 interne au serveur (duels, !duel & co) —
    système indépendant du ranked en jeu Brawl Stars, voir ranked_1v1.
    ?mois=YYYY-MM renvoie une saison archivée (voir db_bs.get_ranked_1v1_season)
    au lieu du mois en cours."""
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    mois = request.args.get("mois")
    entries = []
    if mois:
        for uid_str, p in db_bs.get_ranked_1v1_season(mois).items():
            member = guild.get_member(int(uid_str)) if guild else None
            entries.append({
                "name": member.display_name if member else "Ancien membre",
                "tag": (main.bs_accounts.get(uid_str) or {}).get("tag"),
                "points": p.get("points", 0),
                "wins": p.get("wins", 0),
                "losses": p.get("losses", 0),
                "tier": _r1v1_tier_label(p.get("points", 0), main.RANKED_1V1_TIERS),
            })
    else:
        for uid_str, p in main.ranked_1v1.items():
            member = guild.get_member(int(uid_str)) if guild else None
            if not member or member.bot:
                continue
            entries.append({
                "name": member.display_name,
                "tag": (main.bs_accounts.get(uid_str) or {}).get("tag"),
                "points": p.get("points", 0),
                "wins": p.get("wins", 0),
                "losses": p.get("losses", 0),
                "tier": _r1v1_tier_label(p.get("points", 0), main.RANKED_1V1_TIERS),
            })
    entries.sort(key=lambda e: e["points"], reverse=True)
    return jsonify(entries)


@app.route("/api/famille/saisons_1v1")
def api_famille_saisons_1v1():
    return jsonify(db_bs.list_ranked_1v1_seasons())


@app.route("/api/famille/classement_casino")
def api_famille_classement_casino():
    """Classement de l'économie casino du bot (coins), pour la page Classement du site.
    ?mois=YYYY-MM renvoie une saison archivée (voir db_bs.get_casino_season)
    au lieu du mois en cours."""
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    mois = request.args.get("mois")
    entries = []
    if mois:
        for row in db_bs.get_casino_season(mois):
            uid = row["discord_id"]
            member = guild.get_member(int(uid)) if guild else None
            entries.append({
                "name": member.display_name if member else "Ancien membre",
                "tag": (main.bs_accounts.get(uid) or {}).get("tag"),
                "coins": row["coins"],
            })
    else:
        for uid, amount in main.coins.items():
            member = guild.get_member(int(uid)) if guild else None
            if not member or member.bot:
                continue
            entries.append({
                "name": member.display_name,
                "tag": (main.bs_accounts.get(uid) or {}).get("tag"),
                "coins": amount,
            })
    entries.sort(key=lambda e: e["coins"], reverse=True)
    return jsonify(entries[:100])


@app.route("/api/famille/saisons_casino")
def api_famille_saisons_casino():
    return jsonify(db_bs.list_casino_seasons())


@app.route("/api/famille/joueur/<tag>")
def api_famille_joueur(tag):
    """Profil d'un joueur — agrège les données déjà suivies (trophées, ranked,
    rôle dans le clan, 1v1/casino si un compte Discord est lié) avec un appel
    live à l'API officielle pour les victoires 3v3/solo/duo et le niveau XP
    (pas suivi en continu, peu de volume attendu sur cette route donc pas
    besoin d'un cache dédié). Public comme le reste de l'API famille — le
    tag est déjà visible publiquement sur tous les classements du site."""
    main = _bot()
    clean = tag.strip().lstrip("#").upper()

    trophy_row = next((p for p in db_bs.get_latest_trophies() if p["tag"] == clean), None)
    if trophy_row is None:
        return {"error": "joueur introuvable ou pas encore synchronisé"}, 404

    role = None
    for club in db_bs.list_family_clubs():
        detail = main.bs_family_club_details.get(club["tag"])
        if not detail:
            continue
        member = next((m for m in detail.get("members", []) if m.get("tag") == clean), None)
        if member:
            role = member.get("role")
            break

    ranked_row = next((r for r in db_bs.get_ranked_cache() if r["tag"] == clean), None)

    discord_id = None
    for uid_str, acc in main.bs_accounts.items():
        if (acc.get("tag") or "").strip().lstrip("#").upper() == clean:
            discord_id = uid_str
            break

    duel_1v1 = None
    casino_coins = None
    if discord_id:
        p1v1 = main.ranked_1v1.get(discord_id)
        if p1v1:
            duel_1v1 = {
                "points": p1v1.get("points", 0),
                "wins": p1v1.get("wins", 0),
                "losses": p1v1.get("losses", 0),
                "tier": _r1v1_tier_label(p1v1.get("points", 0), main.RANKED_1V1_TIERS),
            }
        if discord_id in main.coins:
            casino_coins = main.coins[discord_id]

    live = None
    try:
        future = asyncio.run_coroutine_threadsafe(main._bs_fetch_player(f"#{clean}"), main.bot.loop)
        live, _err = future.result(timeout=15)
    except Exception:
        pass  # Stats live indisponibles (API tierce down) — pas bloquant, le reste du profil s'affiche quand même

    profile = db_bs.get_player_profile(clean) or {}

    return jsonify({
        "tag": f"#{clean}",
        "name": trophy_row["name"],
        "club": trophy_row["club"],
        "role": role,
        "trophies": trophy_row["trophies"],
        "ranked_pts": ranked_row["ranked_pts"] if ranked_row else None,
        "ranked_tier": ranked_row["ranked_tier"] if ranked_row else None,
        "highest_ranked_pts": ranked_row.get("highest_ranked_pts") if ranked_row else None,
        "highest_ranked_tier": ranked_row.get("highest_ranked_tier") if ranked_row else None,
        "highest_ranked_rank": ranked_row.get("highest_ranked_rank") if ranked_row else None,
        "duel_1v1": duel_1v1,
        "casino_coins": casino_coins,
        "victories_3v3": live.get("victories_3v3") if live else None,
        "victories_solo": live.get("victories_solo") if live else None,
        "victories_duo": live.get("victories_duo") if live else None,
        "exp_level": live.get("exp_level") if live else None,
        "discord_id": discord_id,
        "bio": profile.get("bio"),
    })


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


# ── Actions admin déclenchées depuis le site (panel /admin) ──────────────
# Le site vérifie déjà tier=admin (getAccessContext) avant d'appeler ces
# routes, protégées ici par le secret partagé — on revérifie quand même
# is_admin via db_members (défense en profondeur, même logique que le reste).

def _require_admin(body: dict):
    """Retourne (discord_id, None) si admin valide, sinon (None, (json, status))."""
    discord_id = str(body.get("discord_id", "")).strip()
    if not discord_id:
        return None, ({"error": "discord_id requis"}, 400)
    member = db_members.get_member(discord_id)
    if not member or not member["is_admin"]:
        return None, ({"error": "Réservé aux administrateurs."}, 403)
    return discord_id, None


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
        "bs_tag": (main.bs_accounts.get(discord_id) or {}).get("tag"),
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


@app.route("/api/profile/<tag>", methods=["POST"])
@_require_internal_secret
def api_profile_update(tag):
    """Mise à jour de la présentation (bio) — le site vérifie déjà côté
    serveur que c'est bien le propriétaire du tag qui modifie (voir
    updatePlayerProfile côté site, discord_id dérivé de la session, jamais
    d'un paramètre client), mais on revérifie ici aussi que discord_id est
    bien lié à ce tag dans bs_accounts avant d'écrire quoi que ce soit — pas
    de confiance aveugle envers un appelant qui aurait juste le secret
    partagé (défense en profondeur, même logique que /api/bslink)."""
    main = _bot()
    clean = tag.strip().lstrip("#").upper()
    discord_id = str(request.form.get("discord_id", "")).strip()
    if not discord_id:
        return {"error": "discord_id requis"}, 400

    acc = main.bs_accounts.get(discord_id)
    linked_tag = (acc.get("tag") or "").strip().lstrip("#").upper() if acc else None
    if linked_tag != clean:
        return {"error": "Ce compte Discord n'est pas lié à ce joueur."}, 403

    if "bio" in request.form:
        bio = request.form.get("bio", "").strip()[:280] or None
        db_bs.upsert_player_profile(clean, bio=bio)

    return jsonify({"ok": True})


@app.route("/api/tickets", methods=["POST"])
@_require_internal_secret
def api_tickets_create():
    """Ouverture d'un ticket depuis le site (formulaire /support, réservé aux
    comptes liés) — même logique que !ticket_panel côté Discord
    (_create_ticket_apply), déclenchée depuis Flask. La création du salon
    passe par des appels Discord (aiohttp + globals partagés) donc DOIT
    s'exécuter sur la boucle du bot, jamais directement ici — même raison que
    /api/bslink."""
    body = request.get_json(silent=True) or {}
    discord_id = str(body.get("discord_id", "")).strip()
    category = str(body.get("category", "")).strip()
    description = str(body.get("description", "")).strip()[:1000]
    bs_tag = str(body.get("bs_tag", "")).strip() or None
    if not discord_id or not category or not description:
        return {"error": "discord_id, category et description requis"}, 400

    main = _bot()
    if not main.bot.is_ready():
        return {"error": "Le bot redémarre, réessaie dans quelques secondes."}, 503
    try:
        future = asyncio.run_coroutine_threadsafe(
            main._create_ticket_apply(discord_id, category, description, bs_tag), main.bot.loop
        )
        data, err = future.result(timeout=20)
    except Exception as e:
        return {"error": f"Erreur interne : {e}"}, 500

    if err:
        return {"error": err}, 400
    return jsonify(data)


@app.route("/api/tickets/<int:ticket_id>")
@_require_internal_secret
def api_ticket_get(ticket_id):
    """Métadonnées + transcript d'un ticket fermé, pour la page staff
    /staff/tickets/[id] du site (lien posté dans #logs-ticket à la
    fermeture). Lecture Supabase directe, pas besoin de la boucle du bot."""
    ticket = db_bs.get_ticket(ticket_id)
    if ticket is None:
        return {"error": "ticket introuvable"}, 404
    return jsonify(ticket)


VALID_NEWS_ICONS = {"skull", "shield", "message", "trophy"}


@app.route("/api/actualites", methods=["POST"])
@_require_internal_secret
def api_actualites_create():
    """Publication d'une actualité depuis le panel staff/admin du site — le
    site vérifie déjà côté serveur que l'appelant est staff/admin
    (getAccessContext), protégé ici par le secret partagé comme le reste
    des routes staff (/api/staff/panel)."""
    body = request.get_json(silent=True) or {}
    icon = str(body.get("icon", "")).strip()
    title = str(body.get("title", "")).strip()[:100]
    description = str(body.get("description", "")).strip()[:300]
    author = str(body.get("author", "")).strip()[:100] or None

    if icon not in VALID_NEWS_ICONS:
        return {"error": "Icône invalide."}, 400
    if not title or not description:
        return {"error": "Titre et description requis."}, 400

    db_bs.create_news(icon, title, description, author)
    return jsonify({"ok": True})


@app.route("/api/admin/economy/status")
@_require_internal_secret
def api_admin_economy_status():
    main = _bot()
    return jsonify({"casino_paused": main.casino_paused, "crypto_market_frozen": main.crypto_market_frozen})


@app.route("/api/admin/casino/pause", methods=["POST"])
@_require_internal_secret
def api_admin_casino_pause():
    body = request.get_json(silent=True) or {}
    _discord_id, err = _require_admin(body)
    if err:
        return err
    main = _bot()
    future = asyncio.run_coroutine_threadsafe(main._apply_casino_pause(), main.bot.loop)
    paused = future.result(timeout=10)
    return jsonify({"ok": True, "paused": paused})


@app.route("/api/admin/casino/resume", methods=["POST"])
@_require_internal_secret
def api_admin_casino_resume():
    body = request.get_json(silent=True) or {}
    _discord_id, err = _require_admin(body)
    if err:
        return err
    main = _bot()
    future = asyncio.run_coroutine_threadsafe(main._apply_casino_resume(), main.bot.loop)
    paused = future.result(timeout=10)
    return jsonify({"ok": True, "paused": paused})


@app.route("/api/admin/casino/ban", methods=["POST"])
@_require_internal_secret
def api_admin_casino_ban():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    future = asyncio.run_coroutine_threadsafe(
        main._apply_casino_ban(guild, int(target), int(discord_id), body.get("reason") or None), main.bot.loop
    )
    return jsonify(future.result(timeout=10))


@app.route("/api/admin/casino/unban", methods=["POST"])
@_require_internal_secret
def api_admin_casino_unban():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    future = asyncio.run_coroutine_threadsafe(
        main._apply_casino_unban(guild, int(target), int(discord_id)), main.bot.loop
    )
    return jsonify(future.result(timeout=10))


@app.route("/api/admin/crypto/freeze", methods=["POST"])
@_require_internal_secret
def api_admin_crypto_freeze():
    body = request.get_json(silent=True) or {}
    _discord_id, err = _require_admin(body)
    if err:
        return err
    main = _bot()
    future = asyncio.run_coroutine_threadsafe(main._apply_crypto_freeze(), main.bot.loop)
    frozen = future.result(timeout=10)
    return jsonify({"ok": True, "frozen": frozen})


@app.route("/api/admin/coins", methods=["POST"])
@_require_internal_secret
def api_admin_coins():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        return {"error": "amount invalide."}, 400
    if not target or amount == 0:
        return {"error": "target_discord_id et amount (non nul) requis"}, 400
    compte = str(body.get("compte", "cash"))
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    future = asyncio.run_coroutine_threadsafe(
        main._apply_coins_adjust(guild, int(target), int(discord_id), amount, compte), main.bot.loop
    )
    result = future.result(timeout=10)
    if "error" in result:
        return result, 404
    return jsonify(result)


def _require_ticket_staff(body: dict):
    """Comme _require_admin, mais accepte aussi le staff ticket (TICKET_STAFF_ROLE_IDS)
    — mêmes rôles que _is_ticket_staff côté bot (main.py)."""
    discord_id = str(body.get("discord_id", "")).strip()
    if not discord_id:
        return None, ({"error": "discord_id requis"}, 400)
    member = db_members.get_member(discord_id)
    main = _bot()
    is_staff = member is not None and (
        member["is_admin"] or any(str(rid) in member["role_ids"] for rid in main.TICKET_STAFF_ROLE_IDS)
    )
    if not is_staff:
        return None, ({"error": "Réservé au staff."}, 403)
    return discord_id, None


def _run_mod_action(main, coro):
    """Exécute une coroutine _apply_* sur la boucle du bot et normalise les
    erreurs réseau/timeout — toutes les routes de modération ci-dessous
    renvoient (data, error) de la même façon."""
    future = asyncio.run_coroutine_threadsafe(coro, main.bot.loop)
    try:
        return future.result(timeout=20)
    except Exception as e:
        return None, f"Erreur interne : {e}"


@app.route("/api/admin/moderation/warn", methods=["POST"])
@_require_internal_secret
def api_admin_mod_warn():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    reason = str(body.get("reason", "")).strip() or "Aucune raison spécifiée"
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    data, apply_err = _run_mod_action(main, main._apply_warn(guild, int(target), int(discord_id), reason))
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/admin/moderation/mute", methods=["POST"])
@_require_internal_secret
def api_admin_mod_mute():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    duration = str(body.get("duration", "")).strip() or None
    reason = str(body.get("reason", "")).strip() or "Aucune raison spécifiée"
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    data, apply_err = _run_mod_action(main, main._apply_mute(guild, int(target), int(discord_id), duration, reason))
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/admin/moderation/unmute", methods=["POST"])
@_require_internal_secret
def api_admin_mod_unmute():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    data, apply_err = _run_mod_action(main, main._apply_unmute(guild, int(target), int(discord_id)))
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/admin/moderation/ban", methods=["POST"])
@_require_internal_secret
def api_admin_mod_ban():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    reason = str(body.get("reason", "")).strip() or None
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    data, apply_err = _run_mod_action(main, main._apply_ban(guild, int(target), int(discord_id), reason))
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/admin/moderation/silence", methods=["POST"])
@_require_internal_secret
def api_admin_mod_silence():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    data, apply_err = _run_mod_action(main, main._apply_silence(guild, int(target), int(discord_id)))
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/admin/moderation/unsilence", methods=["POST"])
@_require_internal_secret
def api_admin_mod_unsilence():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_admin(body)
    if err:
        return err
    target = str(body.get("target_discord_id", "")).strip()
    if not target:
        return {"error": "target_discord_id requis"}, 400
    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    if not guild:
        return {"error": "Serveur introuvable."}, 500
    data, apply_err = _run_mod_action(main, main._apply_unsilence(guild, int(target), int(discord_id)))
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/admin/tickets")
@_require_internal_secret
def api_admin_tickets_list():
    discord_id = request.args.get("discord_id", "")
    _actor, err = _require_ticket_staff({"discord_id": discord_id})
    if err:
        return err
    return jsonify(db_bs.list_open_tickets_full())


@app.route("/api/admin/tickets/fermer", methods=["POST"])
@_require_internal_secret
def api_admin_tickets_fermer():
    body = request.get_json(silent=True) or {}
    discord_id, err = _require_ticket_staff(body)
    if err:
        return err
    try:
        ticket_id = int(body.get("ticket_id"))
    except (TypeError, ValueError):
        return {"error": "ticket_id invalide"}, 400
    reason = body.get("reason") or None

    ticket = db_bs.get_ticket(ticket_id)
    if not ticket:
        return {"error": "Ticket introuvable."}, 404
    if ticket["status"] != "open":
        return {"error": "Ce ticket est déjà fermé."}, 400

    main = _bot()
    guild = main.bot.get_guild(main.BS_FAMILY_GUILD_ID)
    channel = guild.get_channel(int(ticket["channel_id"])) if guild else None
    if not channel:
        db_bs.close_ticket(ticket_id, discord_id, reason, [])
        return jsonify({"ok": True, "channel_deleted": False})

    actor = guild.get_member(int(discord_id))
    if not actor:
        return {"error": "Ton compte Discord est introuvable sur le serveur."}, 400
    future = asyncio.run_coroutine_threadsafe(
        main._finish_ticket_close(channel, actor, guild, ticket_id, ticket, reason), main.bot.loop
    )
    try:
        future.result(timeout=20)
    except Exception as e:
        return {"error": f"Erreur interne : {e}"}, 500
    return jsonify({"ok": True, "channel_deleted": True})


@app.route("/api/admin/clans/ajouter", methods=["POST"])
@_require_internal_secret
def api_admin_clans_ajouter():
    body = request.get_json(silent=True) or {}
    _discord_id, err = _require_admin(body)
    if err:
        return err
    tag = str(body.get("tag", "")).strip()
    if not tag:
        return {"error": "tag requis"}, 400
    main = _bot()
    future = asyncio.run_coroutine_threadsafe(main._apply_bs_famille_add(tag), main.bot.loop)
    try:
        data, apply_err = future.result(timeout=30)
    except Exception as e:
        return {"error": f"Erreur interne : {e}"}, 500
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/admin/clans/retirer", methods=["POST"])
@_require_internal_secret
def api_admin_clans_retirer():
    body = request.get_json(silent=True) or {}
    _discord_id, err = _require_admin(body)
    if err:
        return err
    tag = str(body.get("tag", "")).strip()
    if not tag:
        return {"error": "tag requis"}, 400
    main = _bot()
    future = asyncio.run_coroutine_threadsafe(main._apply_bs_famille_remove(tag), main.bot.loop)
    try:
        data, apply_err = future.result(timeout=30)
    except Exception as e:
        return {"error": f"Erreur interne : {e}"}, 500
    if apply_err:
        return {"error": apply_err}, 400
    return jsonify(data)


@app.route("/api/famille/notes/<slug>")
@_require_internal_secret
def api_famille_notes_get(slug):
    """Notes internes staff sur les membres d'un club — protégé comme le
    reste des routes staff (le site ne demande ces données que pour un
    viewer déjà vérifié staff/admin de ce club, voir getClubNotes)."""
    return jsonify(db_bs.get_notes_for_club(slug))


@app.route("/api/famille/notes/<slug>", methods=["POST"])
@_require_internal_secret
def api_famille_notes_set(slug):
    """Le site a déjà vérifié tier=staff/admin + correspondance de club avant
    d'appeler cette route (voir updateMemberNote côté site) — cette
    correspondance rôle Discord -> club vit côté site, pas ici (voir
    db_members.py). On revérifie seulement ce que le bot peut vérifier :
    que le compte est bien staff ou admin (défense en profondeur, même
    logique que /api/profile/<tag>)."""
    body = request.get_json(silent=True) or {}
    discord_id = str(body.get("discord_id", "")).strip()
    tag = str(body.get("tag", "")).strip()
    note = str(body.get("note", "")).strip()[:300] or None
    if not discord_id or not tag:
        return {"error": "discord_id et tag requis"}, 400

    member = db_members.get_member(discord_id)
    main = _bot()
    is_staff = member is not None and (
        member["is_admin"] or any(str(rid) in member["role_ids"] for rid in main.TICKET_STAFF_ROLE_IDS)
    )
    if not is_staff:
        return {"error": "Réservé au staff."}, 403

    clean_tag = tag.strip().lstrip("#").upper()
    db_bs.set_member_note(clean_tag, slug, note, discord_id)
    return jsonify({"ok": True})


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
