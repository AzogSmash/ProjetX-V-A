from decimal import Decimal, ROUND_FLOOR


MINE_RESOURCE_TYPE = "iron_ore"
MINE_RESOURCE_LABEL = "Minerai de fer"
MINE_EMOJI = "⛏️"

BASE_PRODUCTION_PER_HOUR = 10
PRODUCTION_LEVEL_MULTIPLIER = Decimal("1.35")
BASE_STORAGE_CAPACITY = 100
STORAGE_LEVEL_MULTIPLIER = Decimal("1.5")
MAX_MINE_UPGRADE_LEVEL = 20

UPGRADE_BASE_COSTS = {
    "storage": 250,
    "production": 400,
    "quality": 500,
}
UPGRADE_COST_MULTIPLIER = Decimal("1.8")

UPGRADE_LABELS = {
    "storage": "Stockage",
    "production": "Production",
    "quality": "Qualité",
}
UPGRADE_ALIASES = {
    "storage": "storage",
    "stockage": "storage",
    "production": "production",
    "quality": "quality",
    "qualite": "quality",
    "qualité": "quality",
}


def _scaled_floor(base: int, multiplier: Decimal, exponent: int) -> int:
    if exponent < 0:
        raise ValueError("level must be at least 1")
    value = Decimal(base) * (multiplier ** exponent)
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def get_production_rate(level: int) -> int:
    """Production entière par heure : floor(10 × 1.35^(niveau - 1))."""
    return _scaled_floor(BASE_PRODUCTION_PER_HOUR, PRODUCTION_LEVEL_MULTIPLIER, level - 1)


def get_storage_capacity(level: int) -> int:
    """Capacité entière : floor(100 × 1.5^(niveau - 1))."""
    return _scaled_floor(BASE_STORAGE_CAPACITY, STORAGE_LEVEL_MULTIPLIER, level - 1)


def get_upgrade_cost(upgrade_type: str, current_level: int) -> int:
    """Coût du niveau suivant : floor(coût de base × 1.8^(niveau actuel - 1))."""
    try:
        base_cost = UPGRADE_BASE_COSTS[upgrade_type]
    except KeyError as error:
        raise ValueError(f"unknown mine upgrade type: {upgrade_type!r}") from error
    return _scaled_floor(base_cost, UPGRADE_COST_MULTIPLIER, current_level - 1)


def resolve_upgrade_type(value: str) -> str | None:
    return UPGRADE_ALIASES.get(value.strip().casefold())
