"""Accès Supabase pour le tracking Brawl Stars (clans, historique de
trophées, cache ranked, saisons). Remplace bs_family_clubs/bs_trophy_history/
bs_family_ranked_cache/bs_season_month/bs_season_start_date/
bs_trophy_evolution_history — anciennement des dicts en mémoire persistés
dans data.json (voir incident du 20/07/2026 : ce blob partagé avec le reste
du bot pouvait être écrasé par un état appauvri sans qu'aucun système ne le
détecte).

Chaque fonction ici fait un aller-retour réseau vers Supabase — pas de cache
en mémoire côté bot : Postgres est la seule source de vérité, ce qui élimine
la classe de bug où deux instances du process divergent silencieusement.
"""

import os
from datetime import datetime, timezone
from functools import lru_cache

from supabase import create_client, Client


@lru_cache(maxsize=1)
def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


# ── Clans de la famille ──

def list_family_clubs() -> list[dict]:
    """[{'tag','name','slug','alias'}, ...]"""
    res = get_client().table("bs_family_clubs").select("*").order("tag").execute()
    return res.data


def add_family_club(tag: str, name: str, slug: str, alias: str | None) -> None:
    get_client().table("bs_family_clubs").insert(
        {"tag": tag, "name": name, "slug": slug, "alias": alias}
    ).execute()


def remove_family_club(tag: str) -> None:
    get_client().table("bs_family_clubs").delete().eq("tag", tag).execute()


def club_exists(tag: str) -> bool:
    res = get_client().table("bs_family_clubs").select("tag").eq("tag", tag).execute()
    return len(res.data) > 0


# ── Joueurs + historique quotidien de trophées ──

def upsert_members_snapshot(today: str, club_tag: str, club_name: str, members: list[dict]) -> None:
    """members: [{'tag','name','trophies'}, ...] pour UN clan. Upsert
    bs_players (nom/club à jour) puis le point du jour dans
    bs_trophy_snapshots (idempotent : rejouer le même jour met juste à jour
    la valeur, comme l'ancien `if hist[-1]['date'] == today: update`)."""
    tagged = [m for m in members if m.get("tag")]
    if not tagged:
        return
    client = get_client()
    client.table("bs_players").upsert(
        [{"tag": m["tag"], "name": m["name"], "club_tag": club_tag} for m in tagged]
    ).execute()
    client.table("bs_trophy_snapshots").upsert(
        [
            {"player_tag": m["tag"], "snapshot_date": today, "trophies": m["trophies"]}
            for m in tagged
        ],
        on_conflict="player_tag,snapshot_date",
    ).execute()


def clear_stale_club_members(synced_club_tags: list[str], current_member_tags: list[str]) -> None:
    """Met club_tag à NULL pour tout joueur dont le club_tag pointe vers un
    des clans qui viennent d'être synchronisés avec succès mais qui n'est
    plus dans leur liste de membres actuelle — sinon un joueur parti (sans
    rejoindre un autre clan suivi) reste compté indéfiniment dans l'effectif
    de son ancien club (voir incident du 23/07/2026 : un club à 32 "membres"
    alors que Brawl Stars plafonne à 30). `synced_club_tags` doit être limité
    aux clans dont l'appel API a réussi cette passe — ne jamais y inclure un
    clan en échec, sinon ses vrais membres seraient effacés à tort."""
    if not synced_club_tags:
        return
    client = get_client()
    q = client.table("bs_players").update({"club_tag": None}).in_("club_tag", synced_club_tags)
    if current_member_tags:
        q = q.not_.in_("tag", current_member_tags)
    q.execute()


def get_latest_trophies() -> list[dict]:
    """[{'tag','name','club','trophies','date'}, ...] — dernier point connu
    par joueur, via la vue bs_latest_trophies (voir 002_views.sql)."""
    res = get_client().table("bs_latest_trophies").select("*").execute()
    return res.data


def get_player_history(player_tag: str, since: str | None = None) -> list[dict]:
    """[{'snapshot_date','trophies'}, ...] triés par date croissante."""
    q = (
        get_client()
        .table("bs_trophy_snapshots")
        .select("snapshot_date,trophies")
        .eq("player_tag", player_tag)
        .order("snapshot_date")
    )
    if since:
        q = q.gte("snapshot_date", since)
    return q.execute().data


_PAGE_SIZE = 1000


def _select_all_snapshots_since(since_date: str) -> list[dict]:
    """PostgREST plafonne silencieusement toute requête sans .range() à 1000
    lignes — avec ~230 joueurs et un snapshot par jour, ce plafond est
    dépassé en quelques semaines de saison (1785 lignes constatées le
    28/07/2026, classement pusheurs figé au 25/07 pendant que les trophées
    bruts (via une vue Postgres, pas ce chemin) continuaient d'avancer).
    Pagine par blocs de 1000 jusqu'à épuisement plutôt que de tronquer.

    IMPORTANT : le tri doit être TOTAL et déterministe (snapshot_date +
    player_tag, qui forment la clé primaire), pas seulement snapshot_date.
    Avec ~230 lignes partageant la même date chaque jour, un tri sur la seule
    date laisse Postgres départager les ex æquo dans un ordre arbitraire qui
    peut changer d'une requête à l'autre : à la frontière d'une page (ligne
    1000, 2000…), une ligne pouvait alors être sautée ou dupliquée. Quand
    c'était le snapshot de DÉBUT de saison d'un joueur qui sautait, sa
    baseline devenait un point plus récent et sa progression !evo « repartait
    de 0 » — de façon intermittente, puisque l'ordre était recalculé à chaque
    appel (constaté début 08/2026)."""
    client = get_client()
    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            client.table("bs_trophy_snapshots")
            .select("player_tag,snapshot_date,trophies")
            .gte("snapshot_date", since_date)
            .order("snapshot_date")
            .order("player_tag")
            .range(offset, offset + _PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def save_season_baseline(season_month: str, trophies_by_tag: dict[str, int]) -> None:
    """Capture immuable des trophées de chaque joueur au moment exact du
    reset de saison détecté par check_bs_season — jamais écrasée par les
    syncs quotidiens suivants (contrairement à bs_trophy_snapshots, une
    seule ligne par jour réécrite à chaque sync horaire). Demande du
    07/08/2026, suite à un delta resté à 0 pendant ~14h après un reset :
    "je veux save tout le temps les stats de début de saison en cas de
    problème". Upsert idempotent (safe si rejoué après un redémarrage)."""
    if not trophies_by_tag:
        return
    get_client().table("bs_season_baseline").upsert(
        [
            {"season_month": season_month, "player_tag": tag, "trophies": t}
            for tag, t in trophies_by_tag.items()
        ],
        on_conflict="season_month,player_tag",
    ).execute()


def get_season_baseline(season_month: str) -> dict[str, int]:
    """{'tag': trophées}, ...} — figé au moment du reset (voir save_season_baseline)."""
    res = (
        get_client()
        .table("bs_season_baseline")
        .select("player_tag,trophies")
        .eq("season_month", season_month)
        .execute()
    )
    return {r["player_tag"]: r["trophies"] for r in res.data}


def get_season_evolution(since_date: str, season_month: str | None = None) -> list[dict]:
    """[{'tag','name','club','start','end','delta','joined_note'}, ...] pour
    tous les joueurs ayant au moins un point depuis `since_date`. Équivalent
    de l'ancien _bs_evolution_current_entries, mais borné dans le temps donc
    pas besoin de tout l'historique complet.

    `start` vient de bs_season_baseline (valeur figée au reset) quand elle
    existe pour ce joueur — sinon repli sur le premier snapshot quotidien
    connu (ancien comportement, pour les saisons passées sans baseline ou un
    joueur ajouté au tracking après le reset)."""
    client = get_client()
    snaps = _select_all_snapshots_since(since_date)
    by_player: dict[str, list[dict]] = {}
    for row in snaps:
        by_player.setdefault(row["player_tag"], []).append(row)
    if not by_player:
        return []

    baseline = get_season_baseline(season_month) if season_month else {}

    players = {
        p["tag"]: p
        for p in client.table("bs_players")
        .select("tag,name,club_tag")
        .in_("tag", list(by_player.keys()))
        .execute()
        .data
    }
    club_names = {c["tag"]: c["name"] for c in client.table("bs_family_clubs").select("tag,name").execute().data}

    out = []
    for tag, points in by_player.items():
        first, last = points[0], points[-1]
        p = players.get(tag, {})
        start = baseline.get(tag, first["trophies"])
        out.append(
            {
                "tag": tag,
                "name": p.get("name"),
                "club": club_names.get(p.get("club_tag")),
                "start": start,
                "end": last["trophies"],
                "delta": last["trophies"] - start,
                "joined_note": first["snapshot_date"] if (tag not in baseline and first["snapshot_date"] != since_date) else None,
            }
        )
    return out


def get_baselines_since(since_date: str, season_month: str | None = None) -> dict:
    """{'tag': {'trophies': int, 'date': str}, ...} — premier point connu par
    joueur depuis `since_date`, ou la valeur figée de bs_season_baseline
    quand elle existe (prioritaire, voir get_season_evolution). Utilisé
    quand le delta se calcule contre une valeur de trophées fraîchement
    récupérée en direct (ex: !evo), plutôt que contre le dernier snapshot
    stocké."""
    res = _select_all_snapshots_since(since_date)
    baselines: dict = {}
    for row in res:
        if row["player_tag"] not in baselines:
            baselines[row["player_tag"]] = {"trophies": row["trophies"], "date": row["snapshot_date"]}
    if season_month:
        for tag, trophies in get_season_baseline(season_month).items():
            baselines[tag] = {"trophies": trophies, "date": since_date}
    return baselines


# ── Cache ranked ──

def replace_ranked_cache(entries: list[dict]) -> None:
    """entries: [{'tag','name','club','ranked_pts','ranked_tier',
    'highest_ranked_pts','highest_ranked_tier','highest_ranked_rank'}, ...] —
    remplace tout le cache (comme l'ancien bs_family_ranked_cache.clear()+update)."""
    client = get_client()
    client.table("bs_ranked_cache").delete().neq("player_tag", "").execute()
    if entries:
        client.table("bs_ranked_cache").upsert(
            [
                {
                    "player_tag": e["tag"],
                    "ranked_pts": e["ranked_pts"],
                    "ranked_tier": e["ranked_tier"],
                    "highest_ranked_pts": e.get("highest_ranked_pts"),
                    "highest_ranked_tier": e.get("highest_ranked_tier"),
                    "highest_ranked_rank": e.get("highest_ranked_rank"),
                }
                for e in entries
            ]
        ).execute()


def get_ranked_cache() -> list[dict]:
    """[{'tag','name','club','ranked_pts','ranked_tier','highest_ranked_pts',
    'highest_ranked_tier','highest_ranked_rank'}, ...] — jointure avec
    bs_players/bs_family_clubs pour retrouver nom/club (plus stockés en
    double dans bs_ranked_cache lui-même, contrairement à l'ancien dict)."""
    client = get_client()
    ranked = client.table("bs_ranked_cache").select("*").execute().data
    if not ranked:
        return []
    tags = [r["player_tag"] for r in ranked]
    players = {
        p["tag"]: p
        for p in client.table("bs_players").select("tag,name,club_tag").in_("tag", tags).execute().data
    }
    club_names = {c["tag"]: c["name"] for c in client.table("bs_family_clubs").select("tag,name").execute().data}
    out = []
    for r in ranked:
        p = players.get(r["player_tag"], {})
        out.append(
            {
                "tag": r["player_tag"],
                "name": p.get("name"),
                "club": club_names.get(p.get("club_tag")),
                "ranked_pts": r["ranked_pts"],
                "ranked_tier": r["ranked_tier"],
                "highest_ranked_pts": r.get("highest_ranked_pts"),
                "highest_ranked_tier": r.get("highest_ranked_tier"),
                "highest_ranked_rank": r.get("highest_ranked_rank"),
                "updated_at": r["updated_at"],
            }
        )
    return out


def get_ranked_updated_at() -> str | None:
    res = (
        get_client()
        .table("bs_ranked_cache")
        .select("updated_at")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0]["updated_at"] if res.data else None


# ── État de la saison ──

def get_season_state() -> dict:
    """{'season_month': str|None, 'season_start_date': str|None}"""
    res = get_client().table("bs_season_state").select("season_month,season_start_date").limit(1).execute()
    if not res.data:
        return {"season_month": None, "season_start_date": None}
    return res.data[0]


def set_season_state(season_month: str, season_start_date: str) -> None:
    get_client().table("bs_season_state").update(
        {"season_month": season_month, "season_start_date": season_start_date}
    ).eq("id", True).execute()


# ── Archive des saisons passées ──

def archive_season(season_month: str, entries: list[dict]) -> None:
    """entries: [{'tag','name','club','start','end','delta'}, ...]"""
    if not entries:
        return
    get_client().table("bs_season_archive").upsert(
        [
            {
                "season_month": season_month,
                "player_tag": e["tag"],
                "name": e["name"],
                "club": e.get("club"),
                "start_trophies": e["start"],
                "end_trophies": e["end"],
                "delta": e["delta"],
            }
            for e in entries
        ],
        on_conflict="season_month,player_tag",
    ).execute()


def list_archived_seasons() -> list[str]:
    res = get_client().table("bs_season_archive").select("season_month").execute()
    return sorted({r["season_month"] for r in res.data}, reverse=True)


# ── Ranked 1v1 interne (reset mensuel calendaire, indépendant des saisons BS) ──
# Remplace ranked_1v1_history (anciennement un dict en mémoire persisté dans
# data.json — même classe de fragilité que les champs BS avant leur migration
# Supabase ci-dessus, voir migration one-shot dans main.py/load_data).

def archive_ranked_1v1_season(season_month: str, entries: dict) -> None:
    """entries: discord_id (str) -> {'points','wins','losses'}"""
    if not entries:
        return
    get_client().table("ranked_1v1_seasons").upsert(
        [
            {
                "season_month": season_month,
                "discord_id": uid,
                "points": p.get("points", 0),
                "wins": p.get("wins", 0),
                "losses": p.get("losses", 0),
            }
            for uid, p in entries.items()
        ],
        on_conflict="season_month,discord_id",
    ).execute()


def get_ranked_1v1_season(season_month: str) -> dict:
    """discord_id -> {'points','wins','losses'}"""
    res = get_client().table("ranked_1v1_seasons").select("*").eq("season_month", season_month).execute()
    return {r["discord_id"]: r for r in res.data}


def list_ranked_1v1_seasons() -> list[str]:
    res = get_client().table("ranked_1v1_seasons").select("season_month").execute()
    return sorted({r["season_month"] for r in res.data}, reverse=True)


# ── Casino (économie interne, reset mensuel calendaire) ──
# Avant cette table, le reset (manuel ou automatique) effaçait tout sans
# archive — incident silencieux repéré le 29/07/2026 : perte totale et
# définitive du classement précédent à chaque reset, y compris le reset
# automatique qui ne demandait même pas de confirmation.

def archive_casino_season(season_month: str, entries: dict) -> None:
    """entries: discord_id (str) -> coins (int)"""
    if not entries:
        return
    get_client().table("casino_seasons").upsert(
        [{"season_month": season_month, "discord_id": uid, "coins": amount} for uid, amount in entries.items()],
        on_conflict="season_month,discord_id",
    ).execute()


def get_casino_season(season_month: str) -> list[dict]:
    """[{'discord_id','coins'}, ...] triés par coins décroissant."""
    res = (
        get_client()
        .table("casino_seasons")
        .select("*")
        .eq("season_month", season_month)
        .order("coins", desc=True)
        .execute()
    )
    return res.data


def list_casino_seasons() -> list[str]:
    res = get_client().table("casino_seasons").select("season_month").execute()
    return sorted({r["season_month"] for r in res.data}, reverse=True)


# ── Actualités (publiées par le staff/admin depuis le site) ──

def list_news(limit: int | None = None) -> list[dict]:
    """[{'id','icon','title','description','author','created_at'}, ...],
    plus récentes en premier."""
    q = get_client().table("bs_news").select("*").order("created_at", desc=True)
    if limit:
        q = q.limit(limit)
    return q.execute().data


def create_news(icon: str, title: str, description: str, author: str | None) -> None:
    get_client().table("bs_news").insert(
        {"icon": icon, "title": title, "description": description, "author": author}
    ).execute()


# ── Meilleurs builds (un par brawler, voir supabase/012_best_builds.sql) ──

def list_best_builds() -> list[dict]:
    """Un par brawler, triés par nom — pour la page /best-builds du site et
    la future commande bot."""
    res = get_client().table("best_builds").select("*").order("brawler_name").execute()
    return res.data


def get_best_build(brawler_slug: str) -> dict | None:
    res = get_client().table("best_builds").select("*").eq("brawler_slug", brawler_slug).limit(1).execute()
    return res.data[0] if res.data else None


def upsert_best_build(brawler_slug: str, brawler_name: str, comment: str, updated_by: str | None) -> dict:
    """Un seul build par brawler : un nouvel appel pour le même brawler_slug
    remplace le précédent (upsert sur la clé primaire)."""
    res = (
        get_client()
        .table("best_builds")
        .upsert(
            {
                "brawler_slug": brawler_slug,
                "brawler_name": brawler_name,
                "comment": comment,
                "updated_by": updated_by,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="brawler_slug",
        )
        .execute()
    )
    return res.data[0]


def delete_best_build(brawler_slug: str) -> None:
    get_client().table("best_builds").delete().eq("brawler_slug", brawler_slug).execute()


# ── État de la relance hebdomadaire du tag Brawl Stars ──

def get_bs_tag_reminder_last_sent() -> str | None:
    res = get_client().table("bs_tag_reminder_state").select("last_sent_at").limit(1).execute()
    return res.data[0]["last_sent_at"] if res.data else None


def set_bs_tag_reminder_last_sent() -> None:
    get_client().table("bs_tag_reminder_state").update(
        {"last_sent_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", True).execute()


# ── Profils personnalisés (bio) ──

def get_player_profile(tag: str) -> dict | None:
    """{'bio'} ou None si le joueur n'a rien personnalisé."""
    res = (
        get_client()
        .table("bs_player_profiles")
        .select("bio")
        .eq("player_tag", tag)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


_UNSET = object()


def upsert_player_profile(tag: str, bio=_UNSET) -> None:
    """Met à jour la bio si fournie — `None` l'efface (ex: bio="" côté site
    -> bio=None ici)."""
    payload = {"player_tag": tag}
    if bio is not _UNSET:
        payload["bio"] = bio
    if len(payload) == 1:
        return
    get_client().table("bs_player_profiles").upsert(payload, on_conflict="player_tag").execute()


# ── Notes internes staff sur les membres d'un clan (voir supabase/xxx_member_notes.sql) ──
# Une note par joueur (pas par club) : si un membre change de clan, la note
# suit — pas de reset, cohérent avec le reste (bs_player_profiles est aussi
# indexé par tag seul). club_slug sert seulement à savoir quel staff peut
# l'éditer (vérifié côté site, pas ici — voir commentaire dans db_members.py).

def get_notes_for_club(club_slug: str) -> dict:
    """player_tag -> {'note','updated_by','updated_at'} pour les membres notés d'un club."""
    res = get_client().table("member_notes").select("*").eq("club_slug", club_slug).execute()
    return {row["player_tag"]: row for row in res.data}


def set_member_note(tag: str, club_slug: str, note: str | None, updated_by: str) -> None:
    """note=None ou vide efface la note."""
    if note:
        get_client().table("member_notes").upsert(
            {
                "player_tag": tag,
                "club_slug": club_slug,
                "note": note,
                "updated_by": updated_by,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="player_tag",
        ).execute()
    else:
        get_client().table("member_notes").delete().eq("player_tag", tag).execute()


def get_archived_season(season_month: str) -> list[dict]:
    """[{'tag','name','club','start','end','delta'}, ...]"""
    res = (
        get_client()
        .table("bs_season_archive")
        .select("player_tag,name,club,start_trophies,end_trophies,delta")
        .eq("season_month", season_month)
        .execute()
    )
    return [
        {
            "tag": r["player_tag"],
            "name": r["name"],
            "club": r["club"],
            "start": r["start_trophies"],
            "end": r["end_trophies"],
            "delta": r["delta"],
        }
        for r in res.data
    ]


# ── Tickets (remplace tickets.bot) ──

def create_ticket(discord_id: str, bs_tag: str | None, category: str, description: str, channel_id: str) -> dict:
    res = (
        get_client()
        .table("tickets")
        .insert(
            {
                "discord_id": discord_id,
                "bs_tag": bs_tag,
                "category": category,
                "description": description,
                "channel_id": channel_id,
            }
        )
        .execute()
    )
    return res.data[0]


def get_open_ticket_for_user(discord_id: str) -> dict | None:
    res = (
        get_client()
        .table("tickets")
        .select("*")
        .eq("discord_id", discord_id)
        .eq("status", "open")
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_ticket(ticket_id: int) -> dict | None:
    res = get_client().table("tickets").select("*").eq("id", ticket_id).limit(1).execute()
    return res.data[0] if res.data else None


def get_ticket_by_channel(channel_id: str) -> dict | None:
    res = (
        get_client()
        .table("tickets")
        .select("*")
        .eq("channel_id", channel_id)
        .eq("status", "open")
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_open_tickets() -> list[dict]:
    """Tickets ouverts, pour ré-enregistrer les vues persistantes au démarrage (on_ready)."""
    res = get_client().table("tickets").select("id,channel_id").eq("status", "open").execute()
    return res.data


def list_open_tickets_full() -> list[dict]:
    """Tickets ouverts avec tous les champs, pour l'onglet Tickets du panel
    staff/admin du site (contrairement à list_open_tickets, réservée au
    ré-enregistrement des vues au démarrage)."""
    res = (
        get_client()
        .table("tickets")
        .select("*")
        .eq("status", "open")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


def list_closed_tickets(
    limit: int = 20, offset: int = 0, category: str | None = None, exclude_incident: bool = False,
) -> list[dict]:
    """Tickets fermés (sans le transcript, trop volumineux pour une liste),
    triés par date de fermeture décroissante — pour l'onglet historique du
    panel staff/admin du site. Pagination par limit/offset ; exclude_incident
    exclut la catégorie incident AU NIVEAU DE LA REQUÊTE (pas après coup) pour
    que la pagination reste correcte pour le staff clan qui n'y a pas accès."""
    query = (
        get_client()
        .table("tickets")
        .select("id,discord_id,bs_tag,category,description,status,claimed_by,closed_by,close_reason,created_at,closed_at")
        .eq("status", "closed")
    )
    if category:
        query = query.eq("category", category)
    elif exclude_incident:
        query = query.neq("category", "incident")
    res = query.order("closed_at", desc=True).range(offset, offset + limit - 1).execute()
    return res.data


def count_closed_tickets(category: str | None = None, exclude_incident: bool = False) -> int:
    query = get_client().table("tickets").select("id", count="exact").eq("status", "closed")
    if category:
        query = query.eq("category", category)
    elif exclude_incident:
        query = query.neq("category", "incident")
    res = query.execute()
    return res.count or 0


def claim_ticket(ticket_id: int, staff_discord_id: str) -> None:
    get_client().table("tickets").update({"claimed_by": staff_discord_id}).eq("id", ticket_id).execute()


def close_ticket(ticket_id: int, closed_by: str, reason: str | None, transcript: list[dict]) -> None:
    get_client().table("tickets").update(
        {
            "status": "closed",
            "closed_by": closed_by,
            "close_reason": reason,
            "transcript": transcript,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", ticket_id).execute()


# ── Absences ──

def create_absence(
    discord_id: str, club: str, absence_type: str, start_date: str, return_date: str | None,
    reason: str, missed_event: str | None,
) -> dict:
    """absence_type : 'partielle' (temps de jeu réduit) ou 'totale' (aucune
    connexion) — voir supabase/014_absences_type.sql."""
    res = (
        get_client()
        .table("absences")
        .insert(
            {
                "discord_id": discord_id,
                "club": club,
                "absence_type": absence_type,
                "start_date": start_date,
                "return_date": return_date,
                "reason": reason,
                "missed_event": missed_event,
            }
        )
        .execute()
    )
    return res.data[0]


def get_absence(absence_id: int) -> dict | None:
    res = get_client().table("absences").select("*").eq("id", absence_id).limit(1).execute()
    return res.data[0] if res.data else None


def list_absences(club: str | None = None) -> list[dict]:
    """Toutes les absences, triées par date de début décroissante — pour la
    commande !absences et le panel staff/admin du site. Filtrage par club
    optionnel (le site peut aussi re-trier côté client sur le jeu complet)."""
    query = get_client().table("absences").select("*")
    if club:
        query = query.eq("club", club)
    res = query.order("start_date", desc=True).execute()
    return res.data


def list_absences_for_member(discord_id: str) -> list[dict]:
    """Absences (passées et en cours) d'un seul membre — pour resynchroniser
    le rôle Absent (_sync_absence_role) sans avoir à tout lister."""
    res = get_client().table("absences").select("*").eq("discord_id", discord_id).execute()
    return res.data


def update_absence(
    absence_id: int, absence_type: str, start_date: str, return_date: str | None,
    reason: str, missed_event: str | None,
) -> None:
    get_client().table("absences").update(
        {
            "absence_type": absence_type,
            "start_date": start_date,
            "return_date": return_date,
            "reason": reason,
            "missed_event": missed_event,
        }
    ).eq("id", absence_id).execute()


def delete_absence(absence_id: int) -> None:
    get_client().table("absences").delete().eq("id", absence_id).execute()


# ── Anti-spam de la relance/prompt tag Brawl Stars (onboarding) ──

def get_bs_tag_onboarding_last_prompt(discord_id: str) -> str | None:
    res = (
        get_client()
        .table("bs_tag_onboarding_prompts")
        .select("last_prompted_at")
        .eq("discord_id", discord_id)
        .limit(1)
        .execute()
    )
    return res.data[0]["last_prompted_at"] if res.data else None


def set_bs_tag_onboarding_last_prompt(discord_id: str) -> None:
    get_client().table("bs_tag_onboarding_prompts").upsert(
        {"discord_id": discord_id, "last_prompted_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="discord_id",
    ).execute()
