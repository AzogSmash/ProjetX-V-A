from dataclasses import dataclass


@dataclass(frozen=True)
class IndustrialActor:
    id: int
    actor_type: str
    discord_user_id: int | None
    ai_company_id: int | None


@dataclass(frozen=True)
class IndustrialUser:
    discord_user_id: int
    credits: int
    primary_job: str | None


@dataclass(frozen=True)
class AdminCreditResult:
    status: str
    operation: str
    admin_discord_user_id: int
    target_discord_user_id: int
    amount: int
    balance_before: int
    balance_after: int
    request_id: str
    duplicate_request: bool = False


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


@dataclass(frozen=True)
class Blacksmith:
    owner_discord_user_id: int
    company_id: int
    company_name: str
    forge_level: int
    speed_level: int
    storage_level: int
    yield_level: int
    active_jobs: int
    completed_jobs: int
    reserved_output: int


@dataclass(frozen=True)
class ForgeJob:
    id: int
    owner_discord_user_id: int
    company_id: int
    forge_slot: int
    resource_input: str
    resource_output: str
    input_quantity: int
    output_quantity: int
    started_at: str
    finishes_at: str
    status: str


@dataclass(frozen=True)
class ForgeProcessResult:
    job: ForgeJob
    remaining_input: int
    duplicate_request: bool = False


@dataclass(frozen=True)
class ForgeCollectionResult:
    collected_quantity: int
    inventory_quantity: int
    duplicate_request: bool = False


@dataclass(frozen=True)
class ForgeUpgradeResult:
    blacksmith: Blacksmith
    upgrade_type: str
    previous_level: int
    new_level: int
    cost: int
    balance: int
    duplicate_request: bool = False


@dataclass(frozen=True)
class IngotShipment:
    id: int
    blacksmith_company_id: int
    blacksmith_discord_user_id: int
    merchant_company_id: int
    merchant_discord_user_id: int
    banker_company_id: int
    banker_discord_user_id: int
    resource_type: str
    quantity: int
    status: str
    created_at: str
    accepted_at: str | None = None
    cancelled_at: str | None = None


@dataclass(frozen=True)
class ShipmentResult:
    shipment: IngotShipment
    transport: IndustrialTransport | None = None
    duplicate_request: bool = False


@dataclass(frozen=True)
class Banker:
    owner_discord_user_id: int
    company_id: int
    company_name: str
    credits: int


@dataclass(frozen=True)
class WorldSale:
    id: int
    quantity: int
    unit_price: int
    total_credits: int
    balance_after: int
    created_at: str
    duplicate_request: bool = False


@dataclass(frozen=True)
class DeliveryMission:
    id: int
    transport_id: int
    merchant_discord_user_id: int | None
    merchant_actor_id: int
    resource_type: str
    quantity: int
    status: str
    commission_max: int
    arrival_at: str
    courier_discord_user_id: int | None = None


@dataclass(frozen=True)
class DeliveryProfile:
    discord_user_id: int
    delivery_level: int
    delivery_xp: int
    completed_deliveries: int
    cooldown_until: str | None


@dataclass(frozen=True)
class IndustrialContract:
    id: int
    creator_discord_user_id: int
    accepter_discord_user_id: int | None
    resource_type: str
    quantity: int
    total_price: int
    status: str
    expires_at: str
