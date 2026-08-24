from economy_v2.commands import register_economy_commands
from economy_v2.router import (
    EconomyRouter,
    find_command_name_collisions,
    validate_command_names,
)
from economy_v2.services import IndustrialEconomyService, SupabaseIndustrialEconomyService


def build_economy_router(
    service: IndustrialEconomyService | None = None,
) -> EconomyRouter:
    economy_service = service or SupabaseIndustrialEconomyService()
    router = EconomyRouter(getattr(economy_service, "record_activity", None))
    register_economy_commands(router, economy_service)
    return router


economy_router = build_economy_router()

__all__ = [
    "economy_router",
    "find_command_name_collisions",
    "validate_command_names",
]
