from economy_v2.commands.company import build_company_command
from economy_v2.commands.help import ecohelp_command
from economy_v2.commands.mine import build_mine_command
from economy_v2.commands.market import build_market_command
from economy_v2.commands.next_actions import build_next_actions_command
from economy_v2.commands.merchant import build_merchant_command
from economy_v2.commands.forge import build_forge_command
from economy_v2.commands.wallet import build_wallet_command
from economy_v2.commands.bank import build_bank_command
from economy_v2.commands.admin_money import build_admin_money_command
from economy_v2.commands.delivery import build_delivery_command
from economy_v2.commands.contracts import build_contracts_command
from economy_v2.commands.economy import build_economy_command
from economy_v2.commands.insights import (build_adminlog_command, build_achievements_command,
    build_bilan_command, build_economycheck_command, build_fiche_command,
    build_notifications_command, build_objectives_command, build_orders_command,
    build_partners_command, build_rank_command)
from economy_v2.router import EconomyRouter
from economy_v2.services import IndustrialEconomyService


def register_economy_commands(
    router: EconomyRouter,
    service: IndustrialEconomyService,
) -> None:
    # L'aide est statique et doit rester disponible sans aucune dépendance DB.
    router.register_command("ecohelp", ecohelp_command, track_activity=False)
    router.register_command("wallet", build_wallet_command(service))
    router.register_command("company", build_company_command(service))
    router.register_command("mine", build_mine_command(service))
    router.register_command("market", build_market_command(service))
    router.register_command("merchant", build_merchant_command(service))
    router.register_command("forge", build_forge_command(service))
    router.register_command("bank", build_bank_command(service))
    router.register_command("delivery", build_delivery_command(service))
    router.register_command("contracts", build_contracts_command(service))
    router.register_command("economy", build_economy_command(service))
    admin_money = build_admin_money_command(service)
    router.register_command("adminmoney", admin_money, track_activity=False)
    router.register_command("am", admin_money, track_activity=False)
    next_actions = build_next_actions_command(service)
    router.register_command("next", next_actions, track_activity=False)
    router.register_command("go", next_actions, track_activity=False)
    router.register_command("progress", next_actions, track_activity=False)
    for name, handler in (
        ("fiche", build_fiche_command(service)), ("cv", build_fiche_command(service)),
        ("rank", build_rank_command(service)), ("achievements", build_achievements_command(service)),
        ("succes", build_achievements_command(service)), ("objectives", build_objectives_command(service)),
        ("objectifs", build_objectives_command(service)), ("bilan", build_bilan_command(service)),
        ("indstats", build_bilan_command(service)), ("orders", build_orders_command(service)),
        ("partners", build_partners_command(service)), ("notifications", build_notifications_command(service)),
        ("adminlog", build_adminlog_command(service)), ("economycheck", build_economycheck_command(service)),
    ):
        router.register_command(name, handler, track_activity=False)
