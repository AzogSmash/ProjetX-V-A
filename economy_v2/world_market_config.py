WORLD_INGOT_REFERENCE_PRICE = 80
WORLD_INGOT_MIN_PRICE = 50
WORLD_INGOT_MAX_PRICE = 120
MAX_WORLD_SALE_QUANTITY = 1_000_000


def bounded_world_price(recent_volume: int) -> int:
    """Demande décroissante bornée : -1 CR par tranche de 1 000 lingots/24 h."""
    return max(WORLD_INGOT_MIN_PRICE,
               min(WORLD_INGOT_MAX_PRICE, WORLD_INGOT_REFERENCE_PRICE - recent_volume // 1_000))
