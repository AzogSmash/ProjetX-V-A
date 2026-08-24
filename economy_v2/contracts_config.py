MAX_CONTRACT_QUANTITY = 1_000_000
MAX_CONTRACT_TOTAL = 1_000_000_000
MAX_OPEN_CONTRACTS = 10
DEFAULT_CONTRACT_HOURS = 72


def valid_contract_values(quantity: int, total_price: int) -> bool:
    return 1 <= quantity <= MAX_CONTRACT_QUANTITY and 1 <= total_price <= MAX_CONTRACT_TOTAL
