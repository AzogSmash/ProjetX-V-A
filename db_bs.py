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


def get_season_evolution(since_date: str) -> list[dict]:
    """[{'tag','name','club','start','end','delta','joined_note'}, ...] pour
    tous les joueurs ayant au moins un point depuis `since_date`. Équivalent
    de l'ancien _bs_evolution_current_entries, mais borné dans le temps donc
    pas besoin de tout l'historique complet."""
    client = get_client()
    snaps = (
        client.table("bs_trophy_snapshots")
        .select("player_tag,snapshot_date,trophies")
        .gte("snapshot_date", since_date)
        .order("snapshot_date")
        .execute()
        .data
    )
    by_player: dict[str, list[dict]] = {}
    for row in snaps:
        by_player.setdefault(row["player_tag"], []).append(row)
    if not by_player:
        return []

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
        out.append(
            {
                "tag": tag,
                "name": p.get("name"),
                "club": club_names.get(p.get("club_tag")),
                "start": first["trophies"],
                "end": last["trophies"],
                "delta": last["trophies"] - first["trophies"],
                "joined_note": first["snapshot_date"] if first["snapshot_date"] != since_date else None,
            }
        )
    return out


def get_baselines_since(since_date: str) -> dict:
    """{'tag': {'trophies': int, 'date': str}, ...} — premier point connu par
    joueur depuis `since_date`. Utilisé quand le delta se calcule contre une
    valeur de trophées fraîchement récupérée en direct (ex: !evo), plutôt
    que contre le dernier snapshot stocké."""
    res = (
        get_client()
        .table("bs_trophy_snapshots")
        .select("player_tag,snapshot_date,trophies")
        .gte("snapshot_date", since_date)
        .order("snapshot_date")
        .execute()
        .data
    )
    baselines: dict = {}
    for row in res:
        if row["player_tag"] not in baselines:
            baselines[row["player_tag"]] = {"trophies": row["trophies"], "date": row["snapshot_date"]}
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
