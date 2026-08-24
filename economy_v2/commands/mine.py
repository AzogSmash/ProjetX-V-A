import logging

import discord

from economy_v2.jobs import JOB_TYPES
from economy_v2.mining_config import (
    MAX_MINE_UPGRADE_LEVEL,
    MINE_EMOJI,
    MINE_RESOURCE_LABEL,
    UPGRADE_LABELS,
    get_production_rate,
    get_storage_capacity,
    resolve_upgrade_type,
)
from economy_v2.models import Mine, MineUpgradeResult
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import (
    IndustrialEconomyError,
    IndustrialEconomyService,
    InsufficientIndustrialFundsError,
    MineAccessDeniedError,
    MinerCompanyRequiredError,
    MineUpgradeMaxLevelError,
)


logger = logging.getLogger(__name__)


def build_mine_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def mine_command(context: EconomyCommandContext) -> None:
        try:
            if not context.args:
                mine = await service.get_or_create_mine(context.message.author.id)
                await context.message.channel.send(embed=_mine_embed(mine))
                return

            action = context.args[0].casefold()
            if action == "collect" and len(context.args) == 1:
                await _collect(context, service)
                return
            if action == "upgrade" and len(context.args) == 2:
                await _upgrade(context, service, context.args[1])
                return

            await context.message.channel.send(
                "Syntaxes : `?mine`, `?mine collect`, "
                "`?mine upgrade <production|storage|quality>`."
            )
        except MineAccessDeniedError as error:
            current_job = JOB_TYPES.get(error.current_job) if error.current_job else None
            current_label = current_job.label if current_job else "Aucun métier"
            await context.message.channel.send(
                f"{MINE_EMOJI} Cette commande est réservée aux Mineurs.\n"
                f"Ton métier principal actuel est : **{current_label}**."
            )
        except MinerCompanyRequiredError:
            await context.message.channel.send(
                "Ta société minière principale est introuvable. "
                "Utilise `?company` pour vérifier ton entreprise."
            )
        except IndustrialEconomyError:
            logger.exception(
                "[ECONOMY] Mine operation failed | User: %s",
                context.message.author.id,
            )
            await context.message.channel.send(
                "Une erreur est survenue avec ta mine.\n"
                "Réessaie dans quelques instants."
            )

    return mine_command


def _mine_embed(mine: Mine) -> discord.Embed:
    rate = get_production_rate(mine.production_level)
    capacity = get_storage_capacity(mine.storage_level)
    embed = discord.Embed(
        title=f"{MINE_EMOJI} Mine — {mine.company_name}",
        color=0x7F8C8D,
    )
    embed.add_field(name="Minerai", value=MINE_RESOURCE_LABEL, inline=False)
    embed.add_field(name="Production", value=f"**{rate}** minerai / heure", inline=True)
    embed.add_field(name="Stock", value=f"**{mine.stock} / {capacity}**", inline=True)
    embed.add_field(
        name="Améliorations",
        value=(
            f"Production : niveau {mine.production_level}\n"
            f"Stockage : niveau {mine.storage_level}\n"
            f"Qualité : niveau {mine.quality_level}"
        ),
        inline=False,
    )
    if mine.stock >= capacity:
        embed.description = "⚠️ **Stockage plein — la production est arrêtée.**"
    embed.set_footer(
        text="?mine collect • ?mine upgrade <production|storage|quality>"
    )
    return embed


async def _collect(
    context: EconomyCommandContext,
    service: IndustrialEconomyService,
) -> None:
    result = await service.collect_mine(context.message.author.id)
    if result.collected_quantity == 0:
        await context.message.channel.send(
            "Ta mine ne contient actuellement aucun minerai à récupérer."
        )
        return

    embed = discord.Embed(title="📦 Récolte terminée", color=0x2ECC71)
    embed.add_field(
        name="Minerai récupéré",
        value=f"+{result.collected_quantity} {MINE_RESOURCE_LABEL}",
        inline=False,
    )
    embed.add_field(
        name="Inventaire",
        value=f"{result.inventory.quantity} {MINE_RESOURCE_LABEL}",
        inline=False,
    )
    await context.message.channel.send(embed=embed)


async def _upgrade(
    context: EconomyCommandContext,
    service: IndustrialEconomyService,
    raw_upgrade_type: str,
) -> None:
    upgrade_type = resolve_upgrade_type(raw_upgrade_type)
    if upgrade_type is None:
        await context.message.channel.send(
            "Amélioration invalide. Choisis : `production`, `storage` (`stockage`) "
            "ou `quality` (`qualité`)."
        )
        return

    try:
        result = await service.upgrade_mine(context.message.author.id, upgrade_type)
    except InsufficientIndustrialFundsError as error:
        await context.message.channel.send(
            f"❌ **Fonds insuffisants**\n\n"
            f"Coût : **{error.cost:,} CR**\n"
            f"Ton solde : **{error.balance:,} CR**"
        )
        return
    except MineUpgradeMaxLevelError:
        await context.message.channel.send(
            f"Cette amélioration est déjà au niveau maximum ({MAX_MINE_UPGRADE_LEVEL})."
        )
        return

    await context.message.channel.send(embed=_upgrade_embed(result))


def _upgrade_embed(result: MineUpgradeResult) -> discord.Embed:
    label = UPGRADE_LABELS[result.upgrade_type]
    embed = discord.Embed(title="⬆️ Amélioration effectuée", color=0x3498DB)
    embed.add_field(
        name=label,
        value=f"Niveau {result.previous_level} → Niveau {result.new_level}",
        inline=False,
    )
    if result.upgrade_type == "production":
        embed.add_field(
            name="Nouvelle production",
            value=f"{get_production_rate(result.new_level)} minerai / heure",
            inline=False,
        )
    elif result.upgrade_type == "storage":
        embed.add_field(
            name="Nouveau stockage",
            value=f"{get_storage_capacity(result.new_level)} minerai",
            inline=False,
        )
    else:
        embed.add_field(
            name="Nouvelle qualité",
            value=f"Niveau {result.new_level}",
            inline=False,
        )
    embed.add_field(name="Coût", value=f"{result.cost:,} CR", inline=True)
    embed.add_field(name="Solde restant", value=f"{result.balance:,} CR", inline=True)
    return embed
