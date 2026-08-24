import logging
from datetime import datetime

import discord

from economy_v2.commands.merchant import parse_discord_user_id
from economy_v2.forge_config import (
    FORGE_INPUT_RESOURCE, FORGE_OUTPUT_RESOURCE, FORGE_UPGRADE_LABELS,
    MAX_FORGE_PROCESS_QUANTITY, MAX_FORGE_UPGRADE_LEVEL,
    get_forge_count, get_forge_rate, get_forge_storage_capacity,
    resolve_forge_upgrade,
)
from economy_v2.jobs import JOB_TYPES
from economy_v2.resources import get_resource
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import (
    BlacksmithAccessDeniedError, BlacksmithCompanyRequiredError,
    ForgeProcessError, ForgeUpgradeMaxLevelError, IndustrialEconomyError,
    IndustrialEconomyService, InsufficientIndustrialFundsError, ShipmentError,
)


logger = logging.getLogger(__name__)
USAGE = (
    "`?forge`, `?forge inventory`, `?forge process iron_ore <quantité>`, "
    "`?forge collect`, `?forge jobs`, "
    "`?forge upgrade <forges|speed|storage|yield>`, "
    "`?forge ai-supply iron_ore <quantité>`."
)


def build_forge_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def forge_command(context: EconomyCommandContext) -> None:
        try:
            if not context.args:
                blacksmith = await service.get_or_create_blacksmith(context.message.author.id)
                await context.message.channel.send(embed=_forge_embed(blacksmith))
            elif context.args[0].casefold() == "inventory" and len(context.args) == 1:
                await _inventory(context, service)
            elif context.args[0].casefold() == "process" and len(context.args) == 3:
                await _process(context, service)
            elif context.args[0].casefold() == "collect" and len(context.args) == 1:
                await _collect(context, service)
            elif context.args[0].casefold() == "jobs" and len(context.args) == 1:
                await _jobs(context, service)
            elif context.args[0].casefold() == "upgrade" and len(context.args) == 2:
                await _upgrade(context, service, context.args[1])
            elif context.args[0].casefold() == "shipment":
                await _shipment(context, service)
            elif context.args[0].casefold() == "ai-supply" and len(context.args) == 3:
                await _ai_supply(context, service)
            else:
                await context.message.channel.send(f"Syntaxe invalide.\n{USAGE}")
        except BlacksmithAccessDeniedError as error:
            job = JOB_TYPES.get(error.current_job) if error.current_job else None
            label = job.label if job else "Aucun métier"
            await context.message.channel.send(
                "🔥 Cette commande est réservée aux Forgerons.\n"
                f"Ton métier principal actuel est : **{label}**."
            )
        except BlacksmithCompanyRequiredError:
            await context.message.channel.send(
                "Ton entreprise Forgeron principale est introuvable. Utilise `?company`."
            )
        except IndustrialEconomyError:
            logger.exception("[ECONOMY] Forge operation failed | User: %s", context.message.author.id)
            await context.message.channel.send(
                "Une erreur est survenue avec ta forge.\nRéessaie dans quelques instants."
            )
    return forge_command


async def _ai_supply(context, service) -> None:
    if context.args[1].casefold() != "iron_ore":
        await context.message.channel.send("L'IA de secours fournit uniquement `iron_ore`."); return
    try: quantity = int(context.args[2])
    except ValueError: quantity = 0
    if not 1 <= quantity <= 1_000:
        await context.message.channel.send("Quantité invalide (1 à 1 000)."); return
    try:
        row = await service.purchase_ai_supply(context.message.author.id, "iron_ore", quantity,
                                               f"discord:{context.message.id}")
    except ShipmentError as error:
        messages = {"ai_unavailable": "Aucun fournisseur IA n'est actuellement nécessaire.",
                    "insufficient_funds": f"Fonds insuffisants : **{error.available or 0:,} CR**.",
                    "insufficient_ai_stock": f"Stock IA insuffisant : **{error.available or 0:,}**.",
                    "ai_truck_busy": "Le camion IA de secours est déjà occupé."}
        await context.message.channel.send(messages.get(error.reason, "Approvisionnement IA impossible.")); return
    await context.message.channel.send(
        f"🤖 **Approvisionnement IA lancé**\n{int(row['quantity']):,} Minerai de fer\n"
        f"Coût : **{int(row['total_price']):,} CR**\nArrivée <t:{_discord_timestamp(row['arrival_at'])}:R>.")


async def _shipment(context, service) -> None:
    action = context.args[1].casefold() if len(context.args) > 1 else ""
    if action == "create" and len(context.args) == 5:
        merchant_id = parse_discord_user_id(context.args[2])
        banker_id = parse_discord_user_id(context.args[3])
        try:
            quantity = int(context.args[4])
        except ValueError:
            quantity = 0
        if merchant_id is None or banker_id is None or not 1 <= quantity <= 1_000_000:
            await context.message.channel.send(
                "Syntaxe : `?forge shipment create <marchand> <banquier> <quantité>`."
            )
            return
        try:
            result = await service.create_ingot_shipment(
                context.message.author.id, merchant_id, banker_id, quantity,
                f"discord:{context.message.id}")
        except ShipmentError as error:
            messages = {
                "invalid_merchant": "Le Marchand désigné n'est pas valide.",
                "invalid_banker": "Le Banquier désigné n'est pas valide.",
                "insufficient_inventory": f"Lingots insuffisants. Disponible : **{error.available or 0:,}**.",
            }
            await context.message.channel.send(messages.get(error.reason, "Impossible de préparer cette expédition."))
            return
        shipment = result.shipment
        await context.message.channel.send(
            f"📦 **Expédition #{shipment.id} préparée**\n"
            f"{shipment.quantity:,} Lingot de fer placés en escrow.\n"
            f"Le Marchand désigné peut utiliser `?merchant transport-ingots {shipment.id}`."
        )
        return
    if action == "cancel" and len(context.args) == 3:
        try:
            shipment_id = int(context.args[2])
        except ValueError:
            shipment_id = 0
        if shipment_id < 1:
            await context.message.channel.send("Syntaxe : `?forge shipment cancel <id>`." )
            return
        try:
            result = await service.cancel_ingot_shipment(
                context.message.author.id, shipment_id, f"discord:{context.message.id}")
        except ShipmentError as error:
            messages = {
                "not_found": "Expédition introuvable.",
                "not_owner": "Cette expédition ne t'appartient pas.",
                "already_accepted": "Une expédition acceptée ne peut plus être annulée.",
            }
            await context.message.channel.send(messages.get(error.reason, "Impossible d'annuler cette expédition."))
            return
        await context.message.channel.send(
            f"✅ Expédition **#{result.shipment.id}** annulée. "
            f"**{result.shipment.quantity:,}** lingots ont été remis dans ton inventaire."
        )
        return
    await context.message.channel.send(
        "Syntaxe : `?forge shipment create <marchand> <banquier> <quantité>` "
        "ou `?forge shipment cancel <id>`."
    )


def _discord_timestamp(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(value)


def _forge_embed(blacksmith) -> discord.Embed:
    forge_count = get_forge_count(blacksmith.forge_level)
    storage = get_forge_storage_capacity(blacksmith.storage_level)
    embed = discord.Embed(title=f"🔥 Forge — {blacksmith.company_name}", color=0xE67E22)
    embed.add_field(name="Forges", value=f"{forge_count} ({blacksmith.active_jobs} actives)")
    embed.add_field(name="Vitesse", value=f"{get_forge_rate(blacksmith.speed_level)} minerai / heure / forge")
    embed.add_field(name="Stockage de sortie", value=f"{blacksmith.reserved_output:,} / {storage:,}")
    embed.add_field(name="Prêts à collecter", value=f"{blacksmith.completed_jobs} job(s)", inline=False)
    embed.add_field(name="Rendement", value=f"Niveau {blacksmith.yield_level} — 100 %", inline=False)
    embed.set_footer(text="?forge process iron_ore <quantité> • ?forge collect • ?forge jobs")
    return embed


async def _inventory(context, service) -> None:
    await service.get_or_create_blacksmith(context.message.author.id)
    entries = [entry for entry in await service.get_inventory(context.message.author.id) if entry.quantity > 0]
    lines = []
    for entry in entries:
        resource = get_resource(entry.resource_type)
        lines.append(f"{resource.emoji if resource else '📦'} **{entry.quantity:,}** {resource.label if resource else entry.resource_type}")
    await context.message.channel.send(embed=discord.Embed(
        title="📦 Inventaire Forgeron",
        description="\n".join(lines) or "Ton inventaire industriel est vide.",
        color=0x95A5A6,
    ))


async def _process(context, service) -> None:
    resource = get_resource(context.args[1])
    try:
        quantity = int(context.args[2])
    except ValueError:
        quantity = 0
    if resource is None or resource.resource_type != FORGE_INPUT_RESOURCE:
        await context.message.channel.send("La recette V1 accepte uniquement `iron_ore`.")
        return
    if not 1 <= quantity <= MAX_FORGE_PROCESS_QUANTITY:
        await context.message.channel.send("La quantité doit être comprise entre 1 et 1 000 000.")
        return
    try:
        result = await service.start_forge_job(
            context.message.author.id, resource.resource_type, quantity,
            f"discord:{context.message.id}",
        )
    except ForgeProcessError as error:
        messages = {
            "insufficient_inventory": f"Minerai insuffisant. Disponible : **{error.available or 0:,}**.",
            "no_forge_available": "Toutes tes forges sont actuellement occupées.",
            "storage_full": f"Stockage de sortie insuffisant. Place restante : **{error.available or 0:,}**.",
        }
        await context.message.channel.send(messages.get(error.reason, "Impossible de lancer cette transformation."))
        return
    job = result.job
    finish = _discord_timestamp(job.finishes_at)
    embed = discord.Embed(title="🔥 Transformation lancée", color=0xE67E22)
    embed.add_field(name="Recette", value=f"{job.input_quantity:,} Minerai de fer → {job.output_quantity:,} Lingot de fer", inline=False)
    embed.add_field(name="Forge", value=str(job.forge_slot))
    embed.add_field(name="Fin", value=f"<t:{finish}:R>")
    embed.add_field(name="Minerai restant", value=f"{result.remaining_input:,}", inline=False)
    if result.duplicate_request:
        embed.set_footer(text="Requête déjà traitée : résultat existant renvoyé.")
    await context.message.channel.send(embed=embed)


async def _collect(context, service) -> None:
    result = await service.collect_forge_jobs(
        context.message.author.id, f"discord:{context.message.id}")
    if result.collected_quantity == 0:
        await context.message.channel.send("Aucun lingot terminé n'est actuellement à collecter.")
        return
    embed = discord.Embed(title="📦 Lingots collectés", color=0x2ECC71)
    embed.add_field(name="Collecte", value=f"+{result.collected_quantity:,} Lingot de fer")
    embed.add_field(name="Inventaire", value=f"{result.inventory_quantity:,} Lingot de fer")
    if result.duplicate_request:
        embed.set_footer(text="Requête déjà traitée : résultat existant renvoyé.")
    await context.message.channel.send(embed=embed)


async def _jobs(context, service) -> None:
    jobs = await service.get_forge_jobs(context.message.author.id)
    if not jobs:
        await context.message.channel.send("Aucun job de forge enregistré.")
        return
    labels = {"processing": "en cours", "completed": "terminé", "collected": "collecté"}
    lines = []
    for job in jobs:
        state = labels[job.status]
        if job.status == "processing":
            state += f" — fin <t:{_discord_timestamp(job.finishes_at)}:R>"
        lines.append(f"`#{job.id}` Forge {job.forge_slot} — {job.input_quantity:,} minerai → {job.output_quantity:,} lingots — **{state}**")
    await context.message.channel.send(embed=discord.Embed(
        title="🔥 Jobs de forge récents", description="\n".join(lines), color=0xE67E22))


async def _upgrade(context, service, raw_type: str) -> None:
    upgrade_type = resolve_forge_upgrade(raw_type)
    if upgrade_type is None:
        await context.message.channel.send(
            "Amélioration invalide : `forges`, `speed`, `storage` ou `yield`."
        )
        return
    try:
        result = await service.upgrade_forge(
            context.message.author.id, upgrade_type, f"discord:{context.message.id}")
    except InsufficientIndustrialFundsError as error:
        await context.message.channel.send(
            f"❌ **Fonds insuffisants**\nCoût : **{error.cost:,} CR**\nTon solde : **{error.balance:,} CR**"
        )
        return
    except ForgeUpgradeMaxLevelError:
        await context.message.channel.send(
            f"Cette amélioration est déjà au niveau maximum ({MAX_FORGE_UPGRADE_LEVEL})."
        )
        return
    embed = discord.Embed(title="⬆️ Amélioration de forge effectuée", color=0x2ECC71)
    embed.add_field(name=FORGE_UPGRADE_LABELS[result.upgrade_type],
                    value=f"Niveau {result.previous_level} → Niveau {result.new_level}", inline=False)
    embed.add_field(name="Coût", value=f"{result.cost:,} CR")
    embed.add_field(name="Solde", value=f"{result.balance:,} CR")
    if result.duplicate_request:
        embed.set_footer(text="Requête déjà traitée : résultat existant renvoyé.")
    await context.message.channel.send(embed=embed)
