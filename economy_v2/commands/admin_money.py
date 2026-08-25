import logging
import re

import discord

from economy_v2.admin_money_config import MAX_ADMIN_CREDIT_AMOUNT, SQLITE_INTEGER_MAX
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import IndustrialEconomyError, IndustrialEconomyService


logger = logging.getLogger(__name__)
USER_TOKEN = re.compile(r"(?:<@!?(\d+)>|(\d+))\Z")
USAGE = "Syntaxe : `?adminmoney <add|remove> <@utilisateur|id> <montant>`."


def _is_administrator(message) -> bool:
    permissions = getattr(message.author, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _resolve_member(message, token: str):
    match = USER_TOKEN.fullmatch(token)
    if not match:
        return None
    user_id = int(match.group(1) or match.group(2))
    if user_id < 1 or user_id > SQLITE_INTEGER_MAX:
        return None
    guild = getattr(message, "guild", None)
    if guild is None:
        return None
    return guild.get_member(user_id)


def _parse_amount(token: str) -> int | None:
    if not token.isascii() or not token.isdecimal():
        return None
    amount = int(token)
    if not 1 <= amount <= MAX_ADMIN_CREDIT_AMOUNT:
        return None
    return amount


def build_admin_money_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def admin_money_command(context: EconomyCommandContext) -> None:
        message = context.message
        if not _is_administrator(message):
            await message.channel.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
            return
        if len(context.args) != 3 or context.args[0].casefold() not in {"add", "remove"}:
            await message.channel.send(USAGE)
            return

        operation = context.args[0].casefold()
        target = _resolve_member(message, context.args[1])
        if target is None:
            await message.channel.send("❌ Utilisateur invalide.")
            return
        amount = _parse_amount(context.args[2])
        if amount is None:
            await message.channel.send(
                f"❌ Le montant doit être un entier entre 1 et {MAX_ADMIN_CREDIT_AMOUNT:,} CR."
            )
            return

        try:
            result = await service.adjust_admin_credits(
                message.author.id, target.id, operation, amount, str(message.id),
            )
        except IndustrialEconomyError:
            logger.exception(
                "[ECONOMY ADMIN] operation failed admin=%s target=%s operation=%s amount=%s",
                message.author.id, target.id, operation, amount,
            )
            await message.channel.send("❌ Impossible de modifier les crédits industriels.")
            return

        if result.status == "insufficient_funds":
            await message.channel.send(
                "❌ Solde insuffisant\n\n"
                f"Solde actuel : {result.balance_before:,} CR\n"
                f"Retrait demandé : {amount:,} CR"
            )
            return

        logger.info(
            "[ECONOMY ADMIN] admin=%s target=%s operation=%s amount=%s before=%s after=%s",
            message.author.id, target.id, operation, amount,
            result.balance_before, result.balance_after,
        )
        title = "💳 Crédits ajoutés" if operation == "add" else "💳 Crédits retirés"
        sign = "+" if operation == "add" else "-"
        embed = discord.Embed(title=title, color=0x2ECC71 if operation == "add" else 0xE67E22)
        embed.add_field(name="Utilisateur", value=target.mention, inline=False)
        embed.add_field(name="Montant", value=f"{sign}{amount:,} CR", inline=False)
        embed.add_field(name="Ancien solde", value=f"{result.balance_before:,} CR", inline=False)
        embed.add_field(name="Nouveau solde", value=f"{result.balance_after:,} CR", inline=False)
        await message.channel.send(embed=embed)

    return admin_money_command
