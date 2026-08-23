"""Accès Supabase pour le miroir des membres Discord (voir
supabase/003_discord_members.sql) — alimente les 4 niveaux d'accès du site
(invité / membre de clan / staff / admin). La signification des role_ids
(quel rôle = quel club, staff, etc.) vit côté site ; ce module ne fait que
lire/écrire l'état brut synchronisé depuis le serveur Discord.
"""

from db_bs import get_client


def sync_members(members: list[dict]) -> None:
    """members: [{'discord_id','username','role_ids','is_admin'}, ...].
    Remplace tout le miroir en un coup (comme replace_ranked_cache) — plus
    simple qu'un diff incrémental, et le volume (taille du serveur Discord)
    reste largement dans les clous d'un upsert en un seul batch."""
    if not members:
        return
    client = get_client()
    client.table("discord_members").delete().neq("discord_id", "").execute()
    client.table("discord_members").upsert(
        [
            {
                "discord_id": m["discord_id"],
                "username": m.get("username"),
                "role_ids": m["role_ids"],
                "is_admin": m.get("is_admin", False),
            }
            for m in members
        ]
    ).execute()


def get_member(discord_id: str) -> dict | None:
    """{'discord_id','username','role_ids','is_admin','synced_at'} ou None
    si cet ID n'est pas (plus) membre du serveur Discord."""
    res = (
        get_client()
        .table("discord_members")
        .select("*")
        .eq("discord_id", discord_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None
