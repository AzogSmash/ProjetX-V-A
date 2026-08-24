from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import IndustrialWalletService


def build_wallet_command(wallet_service: IndustrialWalletService) -> EconomyCommandHandler:
    async def wallet_command(context: EconomyCommandContext) -> None:
        balance = await wallet_service.get_balance(context.message.author.id)
        await context.message.channel.send(
            f"💳 **Compte économique industriel**\n"
            f"Crédits industriels : **{balance:,} CR**"
        )

    return wallet_command
