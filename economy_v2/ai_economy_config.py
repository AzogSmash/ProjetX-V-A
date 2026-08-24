AI_BOOTSTRAP_CREDITS = 25_000
AI_ORE_RATE_PER_HOUR = 6
AI_INGOT_RATE_PER_HOUR = 6
AI_STORAGE_CAPACITY = 1_000
AI_ORE_UNIT_PRICE = 12
AI_INGOT_UNIT_PRICE = 100
AI_TRANSPORT_DURATION_SECONDS = 3_600
MAX_AI_PURCHASE_QUANTITY = 1_000


def get_ai_unit_price(resource_type: str) -> int:
    prices = {"iron_ore": AI_ORE_UNIT_PRICE, "iron_ingot": AI_INGOT_UNIT_PRICE}
    if resource_type not in prices:
        raise ValueError("unsupported AI resource")
    return prices[resource_type]
