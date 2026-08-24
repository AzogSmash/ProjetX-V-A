from dataclasses import dataclass


@dataclass(frozen=True)
class IndustrialUser:
    discord_user_id: int
    credits: int
    primary_job: str | None


@dataclass(frozen=True)
class IndustrialCompany:
    id: int
    owner_discord_user_id: int
    name: str
    job_type: str
    level: int
    is_first_company: bool


@dataclass(frozen=True)
class Mine:
    owner_discord_user_id: int
    company_id: int
    company_name: str
    resource_type: str
    stock: int
    storage_level: int
    production_level: int
    quality_level: int
    production_progress: int
    last_production_at: str


@dataclass(frozen=True)
class InventoryEntry:
    owner_discord_user_id: int
    resource_type: str
    quantity: int


@dataclass(frozen=True)
class MineCollectionResult:
    mine: Mine
    collected_quantity: int
    inventory: InventoryEntry


@dataclass(frozen=True)
class MineUpgradeResult:
    mine: Mine
    upgrade_type: str
    previous_level: int
    new_level: int
    cost: int
    balance: int


@dataclass(frozen=True)
class MarketOrder:
    id: int
    owner_discord_user_id: int
    side: str
    resource_type: str
    original_quantity: int
    remaining_quantity: int
    unit_price: int
    status: str
    created_at: str


@dataclass(frozen=True)
class MarketOrderResult:
    order: MarketOrder
    filled_quantity: int
    duplicate_request: bool = False


@dataclass(frozen=True)
class MarketSummary:
    resource_type: str
    average_price_24h: float | None
    low_price_24h: int | None
    high_price_24h: int | None
    volume_24h: int
    sell_orders: tuple[MarketOrder, ...]
    buy_orders: tuple[MarketOrder, ...]


@dataclass(frozen=True)
class Merchant:
    owner_discord_user_id: int
    company_id: int
    company_name: str
    truck_count: int
    truck_capacity_level: int
    truck_speed_level: int
    warehouse_level: int
    active_transports: int


@dataclass(frozen=True)
class IndustrialTransport:
    id: int
    sender_company_id: int
    receiver_company_id: int
    receiver_company_name: str
    merchant_discord_user_id: int
    resource_type: str
    quantity: int
    departure_at: str
    arrival_at: str
    status: str
    truck_slot: int


@dataclass(frozen=True)
class MerchantUpgradeResult:
    merchant: Merchant
    upgrade_type: str
    previous_level: int
    new_level: int
    cost: int
    balance: int
    duplicate_request: bool = False


@dataclass(frozen=True)
class MerchantTransportResult:
    transport: IndustrialTransport
    duplicate_request: bool = False
