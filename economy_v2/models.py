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
