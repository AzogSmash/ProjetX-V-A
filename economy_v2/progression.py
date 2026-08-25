from __future__ import annotations

from dataclasses import dataclass
from math import isqrt

from economy_v2.forge_config import get_forge_upgrade_cost
from economy_v2.merchant_config import get_merchant_upgrade_cost
from economy_v2.mining_config import get_upgrade_cost


RESOURCE_BOOK_VALUES = {"iron_ore": 8, "iron_ingot": 80}
COMPANY_SIZE_THRESHOLDS = (
    (5_000_000, "Empire industriel"),
    (1_000_000, "Groupe industriel"),
    (250_000, "Grande industrie"),
    (50_000, "Entreprise régionale"),
    (10_000, "Entreprise locale"),
    (0, "Petite entreprise"),
)


def _invested_cost(cost_function, upgrades: tuple[str, ...], levels: dict) -> int:
    return sum(
        cost_function(upgrade, level)
        for upgrade in upgrades
        for level in range(1, max(1, int(levels.get(upgrade, 1))))
    )


def infrastructure_value(metrics: dict) -> int:
    mine = metrics.get("mine") or {}
    merchant = metrics.get("merchant") or {}
    forge = metrics.get("forge") or {}
    return (
        _invested_cost(get_upgrade_cost, ("storage", "production", "quality"), mine)
        + _invested_cost(
            get_merchant_upgrade_cost,
            ("trucks", "capacity", "speed", "warehouse"), merchant,
        )
        + _invested_cost(
            get_forge_upgrade_cost,
            ("forges", "speed", "storage", "yield"), forge,
        )
    )


def company_value(metrics: dict) -> int:
    """Non-cashable book value: wallet + fixed inventory + paid infrastructure.

    Lifetime production and transfers are deliberately excluded to prevent
    double-counting assets or inflating value through circular trades.
    """
    inventory_value = sum(
        int(quantity) * RESOURCE_BOOK_VALUES.get(resource, 0)
        for resource, quantity in (metrics.get("inventory") or {}).items()
    )
    return max(0, int(metrics.get("credits", 0))) + inventory_value + infrastructure_value(metrics)


def company_size(value: int) -> str:
    return next(label for threshold, label in COMPANY_SIZE_THRESHOLDS if value >= threshold)


def activity_reputation(metrics: dict) -> int:
    """Progress reputation with diminishing returns and no monetary conversion."""
    produced = int(metrics.get("ore_produced", 0)) + int(metrics.get("ingots_forged", 0))
    logistics = int(metrics.get("transported", 0))
    completed = int(metrics.get("deliveries", 0)) + int(metrics.get("contracts_completed", 0))
    trades = int(metrics.get("market_trade_count", 0))
    persistent = int(metrics.get("reputation_awards", 0))
    return isqrt(max(0, produced)) * 2 + isqrt(max(0, logistics)) + completed * 5 + isqrt(trades) * 3 + persistent


@dataclass(frozen=True)
class AchievementDefinition:
    key: str
    title: str
    metric: str
    threshold: int
    reputation: int


ACHIEVEMENTS = (
    AchievementDefinition("first_ore", "Premier minerai", "ore_produced", 1, 5),
    AchievementDefinition("ore_100", "100 minerais extraits", "ore_produced", 100, 10),
    AchievementDefinition("ore_1000", "1 000 minerais extraits", "ore_produced", 1_000, 25),
    AchievementDefinition("first_ingot", "Premier lingot", "ingots_forged", 1, 5),
    AchievementDefinition("ingot_1000", "1 000 lingots forgés", "ingots_forged", 1_000, 30),
    AchievementDefinition("first_sale", "Première vente", "sales_count", 1, 5),
    AchievementDefinition("trades_100", "100 échanges", "market_trade_count", 100, 25),
    AchievementDefinition("first_delivery", "Première livraison", "deliveries", 1, 5),
    AchievementDefinition("deliveries_100", "100 livraisons", "deliveries", 100, 30),
    AchievementDefinition("first_contract", "Premier contrat", "contracts_completed", 1, 5),
    AchievementDefinition("contracts_50", "50 contrats", "contracts_completed", 50, 25),
    AchievementDefinition("market_100k", "100 000 CR de volume commercial", "market_volume", 100_000, 35),
    AchievementDefinition("millionaire", "Millionnaire industriel", "credits", 1_000_000, 50),
)


OBJECTIVE_DEFINITIONS = {
    "miner": (("produce_ore", "Produire du minerai", "ore_produced"),
              ("sell_ore", "Vendre du minerai", "ore_sold")),
    "merchant": (("buy_resources", "Acheter des ressources", "ore_bought"),
                  ("transport_units", "Transporter des unités", "transported")),
    "blacksmith": (("forge_ingots", "Forger des lingots", "ingots_forged"),
                    ("collect_ingots", "Collecter des lingots", "ingots_forged")),
    "banker": (("sell_ingots", "Vendre des lingots", "ingots_sold"),),
    None: (),
}
COMMON_OBJECTIVES = (
    ("delivery", "Effectuer une livraison", "deliveries"),
    ("contract", "Terminer un contrat", "contracts_completed"),
)
