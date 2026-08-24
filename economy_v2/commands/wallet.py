import discord

from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import IndustrialWalletService


def build_wallet_command(wallet_service: IndustrialWalletService) -> EconomyCommandHandler:
    async def wallet_command(context: EconomyCommandContext) -> None:
        balance = await wallet_service.get_balance(context.message.author.id)
        embed = discord.Embed(
            title="💳 Portefeuille industriel",
            description=f"Solde : **{balance:,} CR**",
            color=0xD68910,
        )
        await context.message.channel.send(embed=embed)

    return wallet_command
