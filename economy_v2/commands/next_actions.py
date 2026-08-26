import logging
import time

import discord

from economy_v2.forge_config import (
    FORGE_UPGRADE_LABELS,
    MAX_FORGE_UPGRADE_LEVEL,
    get_forge_count,
    get_forge_upgrade_cost,
)
from economy_v2.merchant_config import (
    MAX_MERCHANT_UPGRADE_LEVEL,
    MERCHANT_UPGRADE_LABELS,
    get_merchant_upgrade_cost,
)
from economy_v2.mining_config import (
    MAX_MINE_UPGRADE_LEVEL,
    UPGRADE_LABELS,
    get_upgrade_cost,
)
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import IndustrialEconomyError, IndustrialEconomyService


logger = logging.getLogger(__name__)
MAX_RECOMMENDATIONS = 6


def _add(recommendations, priority: int, title: str, details: str, command: str) -> None:
    recommendations.append((priority, title, details, command))


def build_recommendations(snapshot: dict) -> list[tuple[int, str, str, str]]:
    recommendations = []
    job = snapshot["job"]
    wallet = snapshot["wallet"]
    inventory = snapshot["inventory"]
    ore = int(inventory.get("iron_ore", 0))
    ingots = int(inventory.get("iron_ingot", 0))

    if not snapshot["company"]:
        _add(
            recommendations, 120, "🏭 Crée ton entreprise industrielle",
            "Choisis ton métier pour débloquer les activités industrielles.",
            "?company create <métier> <nom>",
        )

    mine = snapshot["mine"]
    if job == "miner" and mine:
        if mine["stock"] > 0:
            _add(
                recommendations, 110, "⛏️ Mine prête à collecter",
                f"Stock : **{mine['stock']:,} / {mine['capacity']:,}**",
                "?mine collect",
            )
        if mine.get("seconds_to_full", 0) > 0:
            minutes = max(1, (mine["seconds_to_full"] + 59) // 60)
            _add(recommendations, 42, "⏱️ Stockage en production", f"Ton stockage sera plein dans environ **{minutes:,} minute(s)**.", "?mine")
        affordable = []
        for upgrade, key in (
            ("storage", "storage_level"),
            ("production", "production_level"),
            ("quality", "quality_level"),
        ):
            level = mine[key]
            if level < MAX_MINE_UPGRADE_LEVEL:
                cost = get_upgrade_cost(upgrade, level)
                if cost <= wallet:
                    affordable.append((cost, upgrade))
        if affordable:
            cost, upgrade = min(affordable)
            _add(
                recommendations, 65, "⬆️ Upgrade disponible",
                f"Solde : **{wallet:,} CR**\n{UPGRADE_LABELS[upgrade]} : **{cost:,} CR**",
                f"?mine upgrade {upgrade}",
            )

    contracts = snapshot["contracts"]
    matching_contracts = int(contracts.get("iron_ore", 0) if ore else 0)
    matching_contracts += int(contracts.get("iron_ingot", 0) if ingots else 0)
    if matching_contracts:
        _add(
            recommendations, 100, "📜 Contrats compatibles",
            f"**{matching_contracts}** contrat(s) recherchent tes ressources.",
            "?contracts",
        )

    if job == "miner" and ore:
        price = snapshot["best_iron_ore_buy_price"]
        if price is not None:
            _add(
                recommendations, 90, "📈 Minerai disponible à la vente",
                f"Inventaire : **{ore:,} iron_ore**\nMeilleur achat : **{price:,} CR / unité**",
                f"?market sell iron_ore {ore} {price}",
            )
        else:
            _add(
                recommendations, 55, "📈 Consulte le marché du minerai",
                f"Inventaire : **{ore:,} iron_ore**\nAucun ordre d'achat immédiat.",
                "?market",
            )

    if snapshot["ready_forge_ingots"]:
        _add(
            recommendations, 115, "🔥 Production de forge terminée",
            f"**{snapshot['ready_forge_ingots']:,}** lingot(s) prêts à récupérer.",
            "?forge collect",
        )
    forge = snapshot["forge"]
    if job == "blacksmith" and forge:
        free_forges = get_forge_count(int(forge["forge_level"])) - int(
            snapshot["processing_forge_jobs"]
        )
        if ore and free_forges > 0:
            _add(
                recommendations, 92, "🔥 Minerai prêt à être forgé",
                f"Inventaire : **{ore:,} iron_ore** • Forges libres : **{free_forges}**",
                f"?forge process iron_ore {ore}",
            )
        if ingots:
            _add(
                recommendations, 82, "📦 Lingots prêts à expédier",
                f"Inventaire : **{ingots:,} iron_ingot**",
                f"?forge shipment create <marchand> <banquier> {ingots}",
            )
        if snapshot["processing_forge_jobs"]:
            _add(
                recommendations, 45, "⏳ Production de forge en cours",
                f"**{snapshot['processing_forge_jobs']}** job(s) en cours.",
                "?forge jobs",
            )
        affordable = []
        for upgrade, key in (
            ("storage", "storage_level"), ("speed", "speed_level"),
            ("yield", "yield_level"), ("forges", "forge_level"),
        ):
            level = int(forge[key])
            if level < MAX_FORGE_UPGRADE_LEVEL:
                cost = get_forge_upgrade_cost(upgrade, level)
                if cost <= wallet:
                    affordable.append((cost, upgrade))
        if affordable:
            cost, upgrade = min(affordable)
            _add(
                recommendations, 65, "⬆️ Upgrade de forge abordable",
                f"{FORGE_UPGRADE_LABELS[upgrade]} : **{cost:,} CR**",
                f"?forge upgrade {upgrade}",
            )

    if snapshot["pending_ingot_shipments"]:
        _add(
            recommendations, 108, "📦 Lingots à transporter",
            f"**{snapshot['pending_ingot_shipments']}** expédition(s) t'attendent.",
            f"?merchant transport-ingots {snapshot['pending_ingot_shipment_id']}",
        )
    merchant = snapshot["merchant"]
    if job == "merchant" and merchant:
        if snapshot["arrived_transports"]:
            _add(
                recommendations, 105, "🚚 Transport arrivé",
                f"**{snapshot['arrived_transports']}** transport(s) ont atteint leur destination.",
                "?merchant transports",
            )
        elif snapshot["active_transports"]:
            _add(
                recommendations, 45, "🚚 Transport en cours",
                f"**{snapshot['active_transports']}** transport(s) à suivre.",
                "?merchant transports",
            )
            arrival = snapshot.get("next_transport_arrival")
            if arrival:
                minutes=max(1,(arrival-int(time.time())+59)//60)
                _add(recommendations, 46, "🚚 Prochaine arrivée", f"Transport attendu dans **{minutes:,} minute(s)**.", "?orders")
        affordable = []
        for upgrade, key in (
            ("warehouse", "warehouse_level"), ("capacity", "truck_capacity_level"),
            ("speed", "truck_speed_level"), ("trucks", "truck_count"),
        ):
            level = int(merchant[key])
            if level < MAX_MERCHANT_UPGRADE_LEVEL:
                cost = get_merchant_upgrade_cost(upgrade, level)
                if cost <= wallet:
                    affordable.append((cost, upgrade))
        if affordable:
            cost, upgrade = min(affordable)
            _add(
                recommendations, 65, "⬆️ Upgrade logistique abordable",
                f"{MERCHANT_UPGRADE_LABELS[upgrade]} : **{cost:,} CR**",
                f"?merchant upgrade {upgrade}",
            )

    if job == "banker" and ingots:
        price = int(snapshot["world_price"])
        _add(
            recommendations, 110, "🏦 Lingots vendables au marché mondial",
            f"Inventaire : **{ingots:,} iron_ingot** • Prix : **{price:,} CR / unité**",
            f"?bank sell iron_ingot {ingots}",
        )
        average=snapshot.get("world_average_24h")
        if average and price>average:
            _add(recommendations, 88, "🏦 Prix bancaire favorable", f"Prix actuel : **{price:,} CR** • moyenne 24h : **{average:.1f} CR**", "?bank market")

    if snapshot["available_delivery_missions"]:
        _add(
            recommendations, 75, "🚚 Mission de livraison disponible",
            f"**{snapshot['available_delivery_missions']}** mission(s) disponible(s).",
            "?delivery list",
        )
    if snapshot["open_market_orders"]:
        _add(
            recommendations, 50, "📋 Ordres de marché ouverts",
            f"Tu as **{snapshot['open_market_orders']}** ordre(s) à suivre.",
            "?market orders",
        )
    objective=snapshot.get("nearest_objective")
    if objective:
        missing=max(0,int(objective["target"])-int(objective["progress"]))
        _add(recommendations, 48, "🎯 Objectif quotidien proche", f"Il te manque **{missing:,}** unité(s) pour le terminer.", "?objectives")
    if snapshot.get("partner_count"):
        _add(recommendations, 35, "🤝 Réseau industriel", f"**{snapshot['partner_count']}** partenaire(s) peuvent faciliter ta prochaine étape.", "?partners")
    if snapshot.get("team_invitations"):
        _add(recommendations, 118, "👥 Invitation d’entreprise", f"**{snapshot['team_invitations']}** invitation(s) t’attendent.", "?equipe")

    recommendations.sort(key=lambda item: (-item[0], item[1], item[3]))
    return recommendations[:MAX_RECOMMENDATIONS]


def build_next_actions_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def next_actions_command(context: EconomyCommandContext) -> None:
        if context.args:
            await context.message.channel.send("Syntaxe : `?next`.")
            return
        try:
            snapshot = await service.get_next_actions_snapshot(context.message.author.id)
        except IndustrialEconomyError:
            logger.exception(
                "[ECONOMY] Next actions failed | User: %s", context.message.author.id,
            )
            await context.message.channel.send("Impossible d'analyser ta progression actuellement.")
            return

        recommendations = build_recommendations(snapshot)
        embed = discord.Embed(title="🧭 Actions disponibles pour progresser", color=0x3498DB)
        if not recommendations:
            embed.description = "Aucune action prioritaire pour le moment. Consulte `?ecohelp`."
        for _, title, details, command in recommendations:
            embed.add_field(
                name=title,
                value=f"{details}\n→ `{command}`",
                inline=False,
            )
        embed.set_footer(text="Recommandations uniquement • aucune action automatique")
        await context.message.channel.send(embed=embed)

    return next_actions_command
