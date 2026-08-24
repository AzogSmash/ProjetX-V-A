from economy_v2.commands.company import build_company_command
from economy_v2.commands.help import ecohelp_command
from economy_v2.commands.mine import build_mine_command
from economy_v2.commands.market import build_market_command
from economy_v2.commands.merchant import build_merchant_command
from economy_v2.commands.wallet import build_wallet_command
from economy_v2.router import EconomyRouter
from economy_v2.services import IndustrialEconomyService


def register_economy_commands(
    router: EconomyRouter,
    service: IndustrialEconomyService,
) -> None:
    router.register_command("ecohelp", ecohelp_command)
    router.register_command("wallet", build_wallet_command(service))
    router.register_command("company", build_company_command(service))
    router.register_command("mine", build_mine_command(service))
    router.register_command("market", build_market_command(service))
    router.register_command("merchant", build_merchant_command(service))
