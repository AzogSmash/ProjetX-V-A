import logging

import discord

from economy_v2.jobs import JOB_TYPES
from economy_v2.resources import get_resource
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import (
    BankerAccessDeniedError, IndustrialEconomyError, IndustrialEconomyService,
    WorldSaleError, ShipmentError,
)
from economy_v2.world_market_config import MAX_WORLD_SALE_QUANTITY


logger = logging.getLogger(__name__)


def build_bank_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def bank_command(context: EconomyCommandContext) -> None:
        try:
            action = context.args[0].casefold() if context.args else ""
            if not action:
                banker = await service.get_or_create_banker(context.message.author.id)
                await context.message.channel.send(embed=discord.Embed(
                    title=f"🏦 Banque — {banker.company_name}",
                    description=f"Solde industriel : **{banker.credits:,} CR**",
                    color=0x2C3E50))
            elif action == "inventory" and len(context.args) == 1:
                await service.get_or_create_banker(context.message.author.id)
                entries = [e for e in await service.get_inventory(context.message.author.id) if e.quantity]
                description = "\n".join(
                    f"{get_resource(e.resource_type).emoji if get_resource(e.resource_type) else '📦'} "
                    f"**{e.quantity:,}** {get_resource(e.resource_type).label if get_resource(e.resource_type) else e.resource_type}"
                    for e in entries) or "Ton inventaire industriel est vide."
                await context.message.channel.send(embed=discord.Embed(
                    title="🏦 Stock bancaire", description=description, color=0x2C3E50))
            elif action == "market" and len(context.args) == 1:
                await service.get_or_create_banker(context.message.author.id)
                market = await service.get_world_market()
                await context.message.channel.send(embed=discord.Embed(
                    title="🌍 Marché mondial — Lingot de fer",
                    description=(f"Prix actuel : **{int(market['current_price']):,} CR**\n"
                                 f"Volume 24 h : **{int(market['volume_24h']):,}**\n"
                                 f"Variation 24 h : **{float(market['change_24h']):+.1f} %**"),
                    color=0x2980B9))
            elif action == "sell" and len(context.args) == 3:
                await _sell(context, service)
            elif action == "history" and len(context.args) == 1:
                sales = await service.get_world_sales(context.message.author.id)
                lines = [f"`#{s.id}` {s.quantity:,} @ {s.unit_price:,} CR — **{s.total_credits:,} CR**"
                         for s in sales]
                await context.message.channel.send(embed=discord.Embed(
                    title="📈 Ventes mondiales", description="\n".join(lines) or "Aucune vente.",
                    color=0x2980B9))
            elif action == "ai-order" and len(context.args) == 3:
                await _ai_order(context, service)
            else:
                await context.message.channel.send(
                    "Syntaxe : `?bank`, `?bank inventory`, `?bank market`, "
                    "`?bank sell iron_ingot <quantité>`, `?bank history`, "
                    "`?bank ai-order iron_ingot <quantité>`."
                )
        except BankerAccessDeniedError as error:
            job = JOB_TYPES.get(error.current_job) if error.current_job else None
            await context.message.channel.send(
                "🏦 Cette commande est réservée aux Banquiers.\n"
                f"Ton métier principal actuel est : **{job.label if job else 'Aucun métier'}**.")
        except IndustrialEconomyError:
            logger.exception("[ECONOMY] Bank operation failed | User: %s", context.message.author.id)
            await context.message.channel.send("Une erreur est survenue avec ta banque. Réessaie plus tard.")
    return bank_command


async def _ai_order(context, service) -> None:
    if context.args[1].casefold() != "iron_ingot":
        await context.message.channel.send("L'IA de secours fournit uniquement `iron_ingot`."); return
    try: quantity = int(context.args[2])
    except ValueError: quantity = 0
    if not 1 <= quantity <= 1_000:
        await context.message.channel.send("Quantité invalide (1 à 1 000)."); return
    try:
        row = await service.purchase_ai_supply(context.message.author.id, "iron_ingot", quantity,
                                               f"discord:{context.message.id}")
    except ShipmentError as error:
        messages = {"ai_unavailable": "Aucun Forgeron IA n'est actuellement nécessaire.",
                    "insufficient_funds": f"Fonds insuffisants : **{error.available or 0:,} CR**.",
                    "insufficient_ai_stock": f"Stock IA insuffisant : **{error.available or 0:,}**.",
                    "ai_truck_busy": "Le camion IA de secours est déjà occupé."}
        await context.message.channel.send(messages.get(error.reason, "Commande IA impossible.")); return
    await context.message.channel.send(
        f"🤖 **Commande IA lancée**\n{int(row['quantity']):,} Lingot de fer\n"
        f"Coût : **{int(row['total_price']):,} CR**.")


async def _sell(context, service) -> None:
    if context.args[1].casefold() != "iron_ingot":
        await context.message.channel.send("Le marché mondial accepte uniquement `iron_ingot`.")
        return
    try: quantity = int(context.args[2])
    except ValueError: quantity = 0
    if not 1 <= quantity <= MAX_WORLD_SALE_QUANTITY:
        await context.message.channel.send("Quantité invalide (1 à 1 000 000).")
        return
    try:
        sale = await service.sell_world_ingots(
            context.message.author.id, quantity, f"discord:{context.message.id}")
    except WorldSaleError as error:
        if error.reason == "insufficient_inventory":
            await context.message.channel.send(f"Lingots insuffisants. Disponible : **{error.available or 0:,}**.")
        else:
            await context.message.channel.send("Cette vente ne peut pas être effectuée.")
        return
    await context.message.channel.send(
        f"🌍 **Vente mondiale effectuée**\n-{sale.quantity:,} Lingot de fer\n"
        f"+{sale.total_credits:,} CR ({sale.unit_price:,} CR/unité)\n"
        f"Solde : **{sale.balance_after:,} CR**")
