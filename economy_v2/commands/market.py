import logging

import discord

from economy_v2.market_config import MARKET_BOOK_DEPTH, validate_market_amounts
from economy_v2.resources import get_resource
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import (
    IndustrialEconomyError, IndustrialEconomyService, MarketAccessDeniedError,
    MarketInsufficientAssetsError, MarketOrderClosedError, MarketOrderLimitError,
    MarketOrderNotFoundError,
)

logger = logging.getLogger(__name__)
USAGE = "`?market`, `?market sell iron_ore <quantité> <prix>`, `?market buy iron_ore <quantité> <prix>`, `?market orders`, `?market cancel <id>`."


def build_market_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def market_command(context: EconomyCommandContext) -> None:
        try:
            if not context.args:
                await _show_market(context, service)
            elif context.args[0].casefold() in {"sell", "buy"}:
                await _create_order(context, service, context.args[0].casefold())
            elif context.args[0].casefold() == "orders" and len(context.args) == 1:
                await _show_orders(context, service)
            elif context.args[0].casefold() == "cancel" and len(context.args) == 2:
                await _cancel(context, service)
            else:
                await context.message.channel.send(f"Syntaxe invalide.\n{USAGE}")
        except MarketAccessDeniedError as error:
            label = "Mineurs" if error.required_job == "miner" else "Marchands"
            await context.message.channel.send(f"Cette opération est réservée aux **{label}**.")
        except MarketInsufficientAssetsError as error:
            await context.message.channel.send(f"❌ Ressources insuffisantes. Disponible : **{error.available:,}**.")
        except MarketOrderLimitError:
            await context.message.channel.send("Tu as atteint la limite de 20 ordres ouverts.")
        except MarketOrderNotFoundError:
            await context.message.channel.send("Ordre introuvable ou appartenant à un autre joueur.")
        except MarketOrderClosedError:
            await context.message.channel.send("Cet ordre est déjà terminé ou annulé.")
        except IndustrialEconomyError:
            logger.exception("[ECONOMY] Market operation failed | User: %s", context.message.author.id)
            await context.message.channel.send("Une erreur est survenue avec le marché industriel.\nRéessaie dans quelques instants.")
    return market_command


def _positive_int(value: str) -> int | None:
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number > 0 else None


async def _create_order(context, service, side: str) -> None:
    if len(context.args) != 4:
        await context.message.channel.send(f"Syntaxe : `?market {side} iron_ore <quantité> <prix_unitaire>`.")
        return
    resource = get_resource(context.args[1])
    quantity, price = _positive_int(context.args[2]), _positive_int(context.args[3])
    if resource is None or not resource.market_enabled:
        await context.message.channel.send("Ressource indisponible sur ce marché. Utilise `iron_ore`.")
        return
    if quantity is None or price is None:
        await context.message.channel.send("La quantité et le prix doivent être des entiers strictement positifs.")
        return
    try:
        validate_market_amounts(quantity, price)
    except ValueError:
        await context.message.channel.send("Limites : quantité et prix entre 1 et 1 000 000.")
        return
    result = await service.create_market_order(
        context.message.author.id, side, resource.resource_type, quantity, price,
        f"discord:{context.message.id}",
    )
    title = "Ordre de vente créé" if side == "sell" else "Ordre d'achat créé"
    embed = discord.Embed(title=f"📈 {title}", color=0x2ECC71)
    embed.add_field(name="Ressource", value=resource.label)
    embed.add_field(name="Quantité", value=f"{quantity:,}")
    embed.add_field(name="Prix", value=f"{price:,} CR / unité")
    embed.add_field(name="Exécuté immédiatement", value=f"{result.filled_quantity:,}", inline=False)
    embed.add_field(name="Reste", value=f"{result.order.remaining_quantity:,}")
    if result.duplicate_request:
        embed.set_footer(text="Requête déjà traitée : résultat existant renvoyé.")
    await context.message.channel.send(embed=embed)


async def _show_orders(context, service) -> None:
    orders = await service.get_market_orders(context.message.author.id)
    if not orders:
        await context.message.channel.send("Tu n'as aucun ordre de marché ouvert.")
        return
    lines = [f"`#{o.id}` {'Vente' if o.side == 'sell' else 'Achat'} — {o.remaining_quantity:,} @ {o.unit_price:,} CR" for o in orders]
    embed = discord.Embed(title="📋 Mes ordres ouverts", description="\n".join(lines), color=0x3498DB)
    embed.set_footer(text="?market cancel <id>")
    await context.message.channel.send(embed=embed)


async def _cancel(context, service) -> None:
    order_id = _positive_int(context.args[1])
    if order_id is None:
        await context.message.channel.send("L'identifiant d'ordre doit être un entier positif.")
        return
    order = await service.cancel_market_order(context.message.author.id, order_id)
    await context.message.channel.send(f"✅ Ordre `#{order.id}` annulé. L'escrow restant a été remboursé.")


async def _show_market(context, service) -> None:
    resource = get_resource("iron_ore")
    summary = await service.get_market_summary(resource.resource_type, MARKET_BOOK_DEPTH)
    avg = f"{summary.average_price_24h:.2f} CR" if summary.average_price_24h is not None else "Aucun échange"
    embed = discord.Embed(title="📈 Marché industriel", description=f"**{resource.label}**", color=0xD68910)
    embed.add_field(name="24 heures", value=f"Moyenne : **{avg}**\nBas : **{summary.low_price_24h or '—'}**\nHaut : **{summary.high_price_24h or '—'}**\nVolume : **{summary.volume_24h:,}**", inline=False)
    sells = "\n".join(f"{o.remaining_quantity:,} @ {o.unit_price:,} CR" for o in summary.sell_orders) or "Aucun"
    buys = "\n".join(f"{o.remaining_quantity:,} @ {o.unit_price:,} CR" for o in summary.buy_orders) or "Aucun"
    embed.add_field(name="Ventes (meilleurs prix)", value=sells)
    embed.add_field(name="Achats (meilleurs prix)", value=buys)
    embed.set_footer(text="?market orders • ordres limités à 20 par joueur")
    await context.message.channel.send(embed=embed)
