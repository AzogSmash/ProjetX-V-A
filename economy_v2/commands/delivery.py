import logging
from datetime import datetime

import discord

from economy_v2.resources import get_resource
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import IndustrialEconomyError, IndustrialEconomyService, ShipmentError


logger = logging.getLogger(__name__)


def _timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def build_delivery_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def delivery_command(context: EconomyCommandContext) -> None:
        try:
            action = context.args[0].casefold() if context.args else "list"
            if action in {"list", "liste"} and len(context.args) <= 1:
                missions = await service.get_delivery_missions()
                lines = []
                for m in missions:
                    resource = get_resource(m.resource_type)
                    lines.append(f"`#{m.id}` {m.quantity:,} {resource.label if resource else m.resource_type} — "
                                 f"jusqu'à **{m.commission_max:,} CR** — arrivée <t:{_timestamp(m.arrival_at)}:R>")
                await context.message.channel.send(embed=discord.Embed(
                    title="🚚 Missions de livraison", description="\n".join(lines) or "Aucune mission disponible.",
                    color=0x16A085))
            elif action == "stats" and len(context.args) == 1:
                p = await service.get_delivery_profile(context.message.author.id)
                await context.message.channel.send(embed=discord.Embed(
                    title="🚚 Statistiques de livraison",
                    description=(f"Niveau : **{p.delivery_level}**\nXP : **{p.delivery_xp:,}**\n"
                                 f"Livraisons : **{p.completed_deliveries:,}**"), color=0x16A085))
            elif action == "accept" and len(context.args) == 2:
                try: mission_id = int(context.args[1])
                except ValueError: mission_id = 0
                if mission_id < 1:
                    await context.message.channel.send("ID de mission invalide."); return
                try:
                    row = await service.accept_delivery(context.message.author.id, mission_id,
                                                        f"discord:{context.message.id}")
                except ShipmentError as error:
                    messages = {"already_taken": "Cette mission a déjà été prise.",
                                "arrived": "Ce transport est déjà arrivé.",
                                "cooldown": "Tu dois attendre la fin de ton cooldown.",
                                "own_transport": "Tu ne peux pas livrer ton propre transport."}
                    await context.message.channel.send(messages.get(error.reason, "Mission indisponible.")); return
                await context.message.channel.send(
                    f"🚚 **Mission acceptée**\nTemps économisé : **{int(row['saved_seconds']) // 60} min**\n"
                    f"Commission : **{int(row['commission_paid']):,} CR**\nXP : **+{int(row['xp_awarded'])}**")
            else:
                await context.message.channel.send(
                    "Syntaxe : `?delivery list`, `?delivery accept <id>`, `?delivery stats`.")
        except IndustrialEconomyError:
            logger.exception("[ECONOMY] Delivery operation failed | User: %s", context.message.author.id)
            await context.message.channel.send("Une erreur est survenue avec les livraisons.")
    return delivery_command
