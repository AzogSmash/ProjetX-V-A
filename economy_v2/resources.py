from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceDefinition:
    resource_type: str
    label: str
    emoji: str
    market_enabled: bool


RESOURCES = {
    "iron_ore": ResourceDefinition("iron_ore", "Minerai de fer", "⛏️", True),
    "iron_ingot": ResourceDefinition("iron_ingot", "Lingot de fer", "🔩", False),
}


def get_resource(resource_type: str) -> ResourceDefinition | None:
    return RESOURCES.get(resource_type.strip().casefold())
