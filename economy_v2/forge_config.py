from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


FORGE_INPUT_RESOURCE = "iron_ore"
FORGE_OUTPUT_RESOURCE = "iron_ingot"
BASE_FORGE_COUNT = 1
BASE_FORGE_RATE_PER_HOUR = 10
FORGE_SPEED_MULTIPLIER = Decimal("1.35")
BASE_FORGE_STORAGE_CAPACITY = 500
FORGE_STORAGE_MULTIPLIER = Decimal("1.5")
MAX_FORGE_UPGRADE_LEVEL = 20
MAX_FORGE_PROCESS_QUANTITY = 1_000_000

FORGE_UPGRADE_BASE_COSTS = {
    "forges": 1_200,
    "speed": 900,
    "storage": 600,
    "yield": 1_000,
}
FORGE_UPGRADE_COST_MULTIPLIER = Decimal("1.8")
FORGE_UPGRADE_LABELS = {
    "forges": "Nombre de forges",
    "speed": "Vitesse de transformation",
    "storage": "Stockage de sortie",
    "yield": "Rendement",
}
FORGE_UPGRADE_ALIASES = {
    "forges": "forges", "forge": "forges",
    "speed": "speed", "vitesse": "speed",
    "storage": "storage", "stockage": "storage",
    "yield": "yield", "rendement": "yield",
}


def _scaled_floor(base: int, multiplier: Decimal, exponent: int) -> int:
    if exponent < 0:
        raise ValueError("level must be at least 1")
    return int((Decimal(base) * multiplier ** exponent).to_integral_value(rounding=ROUND_FLOOR))


def get_forge_count(level: int) -> int:
    if level < 1:
        raise ValueError("level must be at least 1")
    return BASE_FORGE_COUNT + level - 1


def get_forge_rate(level: int) -> int:
    return _scaled_floor(BASE_FORGE_RATE_PER_HOUR, FORGE_SPEED_MULTIPLIER, level - 1)


def get_forge_storage_capacity(level: int) -> int:
    return _scaled_floor(BASE_FORGE_STORAGE_CAPACITY, FORGE_STORAGE_MULTIPLIER, level - 1)


def get_forge_output_quantity(input_quantity: int, yield_level: int) -> int:
    if input_quantity < 0 or yield_level < 1:
        raise ValueError("invalid forge quantity or yield level")
    return input_quantity


def get_forge_duration_seconds(quantity: int, speed_level: int) -> int:
    if quantity < 1:
        raise ValueError("quantity must be positive")
    seconds = Decimal(quantity * 3600) / Decimal(get_forge_rate(speed_level))
    return max(1, int(seconds.to_integral_value(rounding=ROUND_CEILING)))


def get_forge_upgrade_cost(upgrade_type: str, current_level: int) -> int:
    try:
        base = FORGE_UPGRADE_BASE_COSTS[upgrade_type]
    except KeyError as error:
        raise ValueError(f"unknown forge upgrade: {upgrade_type!r}") from error
    return _scaled_floor(base, FORGE_UPGRADE_COST_MULTIPLIER, current_level - 1)


def resolve_forge_upgrade(value: str) -> str | None:
    return FORGE_UPGRADE_ALIASES.get(value.strip().casefold())
