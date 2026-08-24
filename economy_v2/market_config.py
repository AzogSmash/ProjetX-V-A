MARKET_RESOURCE_TYPE = "iron_ore"
MIN_MARKET_QUANTITY = 1
MAX_MARKET_QUANTITY = 1_000_000
MIN_MARKET_UNIT_PRICE = 1
MAX_MARKET_UNIT_PRICE = 1_000_000
MAX_OPEN_MARKET_ORDERS = 20
MARKET_BOOK_DEPTH = 5


def validate_market_amounts(quantity: int, unit_price: int) -> None:
    if not MIN_MARKET_QUANTITY <= quantity <= MAX_MARKET_QUANTITY:
        raise ValueError("invalid market quantity")
    if not MIN_MARKET_UNIT_PRICE <= unit_price <= MAX_MARKET_UNIT_PRICE:
        raise ValueError("invalid market unit price")
