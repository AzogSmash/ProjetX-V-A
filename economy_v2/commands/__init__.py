from economy_v2.commands.help import ecohelp_command
from economy_v2.commands.wallet import build_wallet_command
from economy_v2.router import EconomyRouter
from economy_v2.services import IndustrialWalletService


def register_economy_commands(
    router: EconomyRouter,
    wallet_service: IndustrialWalletService,
) -> None:
    router.register_command("ecohelp", ecohelp_command)
    router.register_command("wallet", build_wallet_command(wallet_service))
