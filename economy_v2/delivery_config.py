MAX_DELIVERY_LEVEL = 100
BASE_DELIVERY_COMMISSION_PER_UNIT = 2
MIN_DELIVERY_COMMISSION = 20
MAX_DELIVERY_COMMISSION = 5_000
BASE_DELIVERY_XP = 20


def get_delivery_level(xp: int) -> int:
    """Progression quadratique bornée : niveau n à 100*(n-1)^2 XP."""
    return min(MAX_DELIVERY_LEVEL, 1 + int((max(0, xp) // 100) ** 0.5))


def get_delivery_reduction_seconds(level: int) -> int:
    """3 minutes/niveau, plafonné à 30 min dès le niveau 10."""
    return min(1_800, max(1, level) * 180)


def get_delivery_cooldown_seconds(level: int) -> int:
    return max(300, 1_800 - (max(1, level) - 1) * 60)


def get_delivery_xp(saved_seconds: int) -> int:
    return BASE_DELIVERY_XP + max(0, saved_seconds) // 60


def get_max_delivery_commission(quantity: int) -> int:
    return max(MIN_DELIVERY_COMMISSION,
               min(MAX_DELIVERY_COMMISSION, quantity * BASE_DELIVERY_COMMISSION_PER_UNIT))
