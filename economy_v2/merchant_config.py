from decimal import Decimal, ROUND_FLOOR


BASE_TRUCK_COUNT = 1
BASE_TRUCK_CAPACITY = 100
TRUCK_CAPACITY_MULTIPLIER = Decimal("1.5")
BASE_TRIP_DURATION_SECONDS = 3600
TRUCK_SPEED_MULTIPLIER = Decimal("0.90")
MIN_TRIP_DURATION_SECONDS = 900
BASE_WAREHOUSE_CAPACITY = 1_000
WAREHOUSE_CAPACITY_MULTIPLIER = Decimal("1.5")
MAX_MERCHANT_UPGRADE_LEVEL = 20
MAX_TRANSPORT_QUANTITY = 1_000_000

MERCHANT_UPGRADE_BASE_COSTS = {
    "trucks": 1_000,
    "capacity": 600,
    "speed": 800,
    "warehouse": 500,
}
MERCHANT_UPGRADE_COST_MULTIPLIER = Decimal("1.8")
MERCHANT_UPGRADE_LABELS = {
    "trucks": "Camions",
    "capacity": "Capacité des camions",
    "speed": "Vitesse des camions",
    "warehouse": "Entrepôt",
}
MERCHANT_UPGRADE_ALIASES = {
    "trucks": "trucks", "truck": "trucks", "camion": "trucks", "camions": "trucks",
    "capacity": "capacity", "capacite": "capacity", "capacité": "capacity",
    "speed": "speed", "vitesse": "speed",
    "warehouse": "warehouse", "entrepot": "warehouse", "entrepôt": "warehouse",
}


def _scaled_floor(base: int, multiplier: Decimal, exponent: int) -> int:
    if exponent < 0:
        raise ValueError("level must be at least 1")
    return int((Decimal(base) * multiplier ** exponent).to_integral_value(rounding=ROUND_FLOOR))


def get_truck_capacity(level: int) -> int:
    return _scaled_floor(BASE_TRUCK_CAPACITY, TRUCK_CAPACITY_MULTIPLIER, level - 1)


def get_trip_duration_seconds(level: int) -> int:
    scaled = _scaled_floor(BASE_TRIP_DURATION_SECONDS, TRUCK_SPEED_MULTIPLIER, level - 1)
    return max(MIN_TRIP_DURATION_SECONDS, scaled)


def get_warehouse_capacity(level: int) -> int:
    return _scaled_floor(BASE_WAREHOUSE_CAPACITY, WAREHOUSE_CAPACITY_MULTIPLIER, level - 1)


def get_merchant_upgrade_cost(upgrade_type: str, current_level: int) -> int:
    try:
        base = MERCHANT_UPGRADE_BASE_COSTS[upgrade_type]
    except KeyError as error:
        raise ValueError(f"unknown merchant upgrade: {upgrade_type!r}") from error
    return _scaled_floor(base, MERCHANT_UPGRADE_COST_MULTIPLIER, current_level - 1)


def resolve_merchant_upgrade(value: str) -> str | None:
    return MERCHANT_UPGRADE_ALIASES.get(value.strip().casefold())
