import logging
import re
from datetime import datetime

import discord

from economy_v2.jobs import JOB_TYPES
from economy_v2.merchant_config import (
    MAX_MERCHANT_UPGRADE_LEVEL, MAX_TRANSPORT_QUANTITY,
    MERCHANT_UPGRADE_LABELS, get_merchant_upgrade_cost, get_trip_duration_seconds,
    get_truck_capacity, get_warehouse_capacity, resolve_merchant_upgrade,
)
from economy_v2.resources import get_resource
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import (
    IndustrialEconomyError, IndustrialEconomyService,
    InsufficientIndustrialFundsError, MerchantAccessDeniedError,
    MerchantCompanyRequiredError, MerchantTransportError,
    MerchantUpgradeMaxLevelError, ShipmentError,
)


logger = logging.getLogger(__name__)
USAGE = (
    "`?merchant`, `?merchant inventory`, `?merchant transports`, "
    "`?merchant upgrade <trucks|capacity|speed|warehouse>`, "
    "`?merchant transport <@forgeron|id> iron_ore <quantité>`."
)


def build_merchant_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def merchant_command(context: EconomyCommandContext) -> None:
        try:
            if not context.args:
                merchant = await service.get_or_create_merchant(context.message.author.id)
                await context.message.channel.send(embed=_merchant_embed(merchant))
            elif context.args[0].casefold() == "inventory" and len(context.args) == 1:
                await _inventory(context, service)
            elif context.args[0].casefold() == "transports" and len(context.args) == 1:
                await _transports(context, service)
            elif context.args[0].casefold() == "upgrade" and len(context.args) == 2:
                await _upgrade(context, service, context.args[1])
            elif context.args[0].casefold() == "transport" and len(context.args) == 4:
                await _start_transport(context, service)
            elif context.args[0].casefold() == "transport-ingots" and len(context.args) == 2:
                await _accept_ingot_shipment(context, service)
            else:
                await context.message.channel.send(f"Syntaxe invalide.\n{USAGE}")
        except MerchantAccessDeniedError as error:
            job = JOB_TYPES.get(error.current_job) if error.current_job else None
            label = job.label if job else "Aucun métier"
            await context.message.channel.send(
                "🚚 Cette commande est réservée aux Marchands.\n"
                f"Ton métier principal actuel est : **{label}**."
            )
        except MerchantCompanyRequiredError:
            await context.message.channel.send(
                "Ton entreprise Marchand principale est introuvable. Utilise `?company`."
            )
        except IndustrialEconomyError:
            logger.exception("[ECONOMY] Merchant operation failed | User: %s", context.message.author.id)
            await context.message.channel.send(
                "Une erreur est survenue avec ton entreprise de transport.\n"
                "Réessaie dans quelques instants."
            )
    return merchant_command


async def _accept_ingot_shipment(context, service) -> None:
    try:
        shipment_id = int(context.args[1])
    except ValueError:
        shipment_id = 0
    if shipment_id < 1:
        await context.message.channel.send("Syntaxe : `?merchant transport-ingots <shipment_id>`." )
        return
    try:
        result = await service.accept_ingot_shipment(
            context.message.author.id, shipment_id, f"discord:{context.message.id}")
    except ShipmentError as error:
        messages = {
            "not_found": "Expédition introuvable.",
            "not_designated_merchant": "Cette expédition est réservée à un autre Marchand.",
            "already_cancelled": "Cette expédition a été annulée.",
            "already_accepted": "Cette expédition a déjà été acceptée.",
            "capacity_exceeded": f"Le chargement dépasse la capacité du camion : **{error.available or 0:,}**.",
            "no_truck_available": "Aucun camion n'est actuellement disponible.",
            "insufficient_commission_funds": (
                f"CR insuffisants pour réserver la commission de livraison : "
                f"solde **{error.available or 0:,} CR**."
            ),
        }
        await context.message.channel.send(messages.get(error.reason, "Impossible d'accepter cette expédition."))
        return
    transport = result.transport
    await context.message.channel.send(
        f"🚚 **Expédition #{result.shipment.id} chargée**\n"
        f"{result.shipment.quantity:,} Lingot de fer vers le Banquier désigné.\n"
        f"Arrivée <t:{_discord_timestamp(transport.arrival_at)}:R>."
    )


def parse_discord_user_id(value: str) -> int | None:
    match = re.fullmatch(r"(?:<@!?)?(\d{1,20})>?", value.strip())
    return int(match.group(1)) if match else None


def _discord_timestamp(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(value)


def _merchant_embed(merchant) -> discord.Embed:
    capacity = get_truck_capacity(merchant.truck_capacity_level)
    duration = get_trip_duration_seconds(merchant.truck_speed_level) // 60
    warehouse = get_warehouse_capacity(merchant.warehouse_level)
    embed = discord.Embed(title=f"🚚 Marchand — {merchant.company_name}", color=0xF39C12)
    embed.add_field(name="Camions", value=f"{merchant.truck_count} ({merchant.active_transports} en route)")
    embed.add_field(name="Capacité", value=f"{capacity:,} unités / camion")
    embed.add_field(name="Trajet de base", value=f"{duration} minutes")
    embed.add_field(name="Entrepôt", value=f"{warehouse:,} unités", inline=False)
    embed.set_footer(text="?merchant transports • ?merchant upgrade <type>")
    return embed


async def _inventory(context, service) -> None:
    await service.get_or_create_merchant(context.message.author.id)
    inventory = await service.get_inventory(context.message.author.id)
    entries = [entry for entry in inventory if entry.quantity > 0]
    description = "\n".join(
        f"{get_resource(entry.resource_type).emoji if get_resource(entry.resource_type) else '📦'} "
        f"**{entry.quantity:,}** {get_resource(entry.resource_type).label if get_resource(entry.resource_type) else entry.resource_type}"
        for entry in entries
    ) or "Ton inventaire industriel est vide."
    await context.message.channel.send(embed=discord.Embed(
        title="📦 Inventaire Marchand", description=description, color=0x3498DB))


async def _transports(context, service) -> None:
    transports = await service.get_merchant_transports(context.message.author.id)
    if not transports:
        await context.message.channel.send("Tu n'as encore aucun transport.")
        return
    lines = []
    for transport in transports:
        resource = get_resource(transport.resource_type)
        if transport.status == "in_transit":
            arrival = _discord_timestamp(transport.arrival_at)
            state = f"arrivée <t:{arrival}:R>"
        else:
            state = "livré"
        lines.append(
            f"`#{transport.id}` Camion {transport.truck_slot} — "
            f"{transport.quantity:,} {resource.label if resource else transport.resource_type} "
            f"→ **{transport.receiver_company_name}** ({state})"
        )
    await context.message.channel.send(embed=discord.Embed(
        title="🚚 Transports récents", description="\n".join(lines), color=0xF39C12))


async def _upgrade(context, service, raw_type: str) -> None:
    upgrade_type = resolve_merchant_upgrade(raw_type)
    if upgrade_type is None:
        await context.message.channel.send(
            "Amélioration invalide : `trucks`, `capacity`, `speed` ou `warehouse`."
        )
        return
    try:
        result = await service.upgrade_merchant(
            context.message.author.id, upgrade_type, f"discord:{context.message.id}")
    except InsufficientIndustrialFundsError as error:
        await context.message.channel.send(
            f"❌ **Fonds insuffisants**\nCoût : **{error.cost:,} CR**\n"
            f"Ton solde : **{error.balance:,} CR**"
        )
        return
    except MerchantUpgradeMaxLevelError:
        await context.message.channel.send(
            f"Cette amélioration est déjà au niveau maximum ({MAX_MERCHANT_UPGRADE_LEVEL})."
        )
        return
    embed = discord.Embed(title="⬆️ Amélioration Marchand effectuée", color=0x2ECC71)
    embed.add_field(name=MERCHANT_UPGRADE_LABELS[result.upgrade_type],
                    value=f"Niveau {result.previous_level} → Niveau {result.new_level}", inline=False)
    embed.add_field(name="Coût", value=f"{result.cost:,} CR")
    embed.add_field(name="Solde", value=f"{result.balance:,} CR")
    if result.duplicate_request:
        embed.set_footer(text="Requête déjà traitée : résultat existant renvoyé.")
    await context.message.channel.send(embed=embed)


async def _start_transport(context, service) -> None:
    receiver_id = parse_discord_user_id(context.args[1])
    resource = get_resource(context.args[2])
    try:
        quantity = int(context.args[3])
    except ValueError:
        quantity = 0
    if receiver_id is None:
        await context.message.channel.send("Indique une mention ou un ID Discord de Forgeron valide.")
        return
    if resource is None or resource.resource_type != "iron_ore":
        await context.message.channel.send("Seul `iron_ore` peut être transporté vers un Forgeron en Phase 2.")
        return
    if not 1 <= quantity <= MAX_TRANSPORT_QUANTITY:
        await context.message.channel.send("La quantité doit être comprise entre 1 et 1 000 000.")
        return
    try:
        result = await service.start_transport(
            context.message.author.id, receiver_id, resource.resource_type, quantity,
            f"discord:{context.message.id}",
        )
    except MerchantTransportError as error:
        messages = {
            "invalid_receiver": "Le destinataire ne possède pas d'entreprise Forgeron valide.",
            "insufficient_inventory": f"Stock insuffisant. Disponible : **{error.available or 0:,}**.",
            "capacity_exceeded": f"Ce chargement dépasse la capacité du camion : **{error.available or 0:,}**.",
            "no_truck_available": "Aucun camion n'est actuellement disponible.",
            "insufficient_commission_funds": (
                f"CR insuffisants pour réserver la commission de livraison : "
                f"solde **{error.available or 0:,} CR**."
            ),
        }
        await context.message.channel.send(messages.get(error.reason, "Impossible de démarrer ce transport."))
        return
    transport = result.transport
    arrival = _discord_timestamp(transport.arrival_at)
    embed = discord.Embed(title="🚚 Transport lancé", color=0x2ECC71)
    embed.add_field(name="Destination", value=transport.receiver_company_name)
    embed.add_field(name="Chargement", value=f"{transport.quantity:,} {resource.label}")
    embed.add_field(name="Camion", value=str(transport.truck_slot))
    embed.add_field(name="Arrivée", value=f"<t:{arrival}:R>", inline=False)
    if result.duplicate_request:
        embed.set_footer(text="Requête déjà traitée : résultat existant renvoyé.")
    await context.message.channel.send(embed=embed)
