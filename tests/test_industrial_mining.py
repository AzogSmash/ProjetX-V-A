import asyncio
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.mining_config import (
    MAX_MINE_UPGRADE_LEVEL,
    get_production_rate,
    get_storage_capacity,
    get_upgrade_cost,
    resolve_upgrade_type,
)
from economy_v2.models import InventoryEntry, Mine, MineCollectionResult, MineUpgradeResult
from economy_v2.services import (
    InsufficientIndustrialFundsError,
    MineUpgradeMaxLevelError,
    SupabaseIndustrialEconomyService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, content: str, user_id: int = 123) -> None:
        self.content = content
        self.author = SimpleNamespace(id=user_id, mention=f"<@{user_id}>")
        self.channel = FakeChannel()


class MiningRepository:
    def __init__(self, job: str = "miner", credits: int = 0) -> None:
        self.job = job
        self.company_job = job
        self.company_name = "Azog Industries"
        self.credits = credits
        self.now_seconds = 0
        self.mine = None
        self.inventory = 0
        self._lock = threading.RLock()

    def advance(self, seconds: int) -> None:
        self.now_seconds += seconds

    def _refresh(self, user_id: int):
        if self.job != "miner":
            return "not_miner", self.job, None
        if self.company_job != "miner":
            return "no_miner_company", self.job, None
        if self.mine is None:
            self.mine = Mine(
                user_id, 1, self.company_name, "iron_ore", 0, 1, 1, 1, 0, "0"
            )
        elapsed = self.now_seconds - int(self.mine.last_production_at)
        rate = get_production_rate(self.mine.production_level)
        capacity = get_storage_capacity(self.mine.storage_level)
        progress = self.mine.production_progress + elapsed * rate
        produced = progress // 3600
        if self.mine.stock >= capacity or self.mine.stock + produced >= capacity:
            stock = capacity
            remainder = 0
        else:
            stock = self.mine.stock + produced
            remainder = progress % 3600
        self.mine = replace(
            self.mine,
            stock=stock,
            production_progress=remainder,
            last_production_at=str(self.now_seconds),
        )
        return "ok", self.job, self.mine

    def get_or_create_and_refresh_mine(self, user_id: int):
        with self._lock:
            return self._refresh(user_id)

    def collect_mine(self, user_id: int):
        with self._lock:
            status, job, mine = self._refresh(user_id)
            if status != "ok":
                return status, job, None
            collected = mine.stock
            self.inventory += collected
            self.mine = replace(mine, stock=0)
            result = MineCollectionResult(
                self.mine,
                collected,
                InventoryEntry(user_id, "iron_ore", self.inventory),
            )
            return "ok", job, result

    def upgrade_mine(self, user_id: int, upgrade_type: str):
        with self._lock:
            status, job, mine = self._refresh(user_id)
            if status != "ok":
                return status, job, None, None, None
            field = f"{upgrade_type}_level"
            old_level = getattr(mine, field)
            if old_level >= MAX_MINE_UPGRADE_LEVEL:
                return "max_level", job, None, self.credits, None
            cost = get_upgrade_cost(upgrade_type, old_level)
            if self.credits < cost:
                return "insufficient_funds", job, cost, self.credits, None
            self.credits -= cost
            self.mine = replace(mine, **{field: old_level + 1})
            result = MineUpgradeResult(
                self.mine, upgrade_type, old_level, old_level + 1, cost, self.credits
            )
            return "ok", job, cost, self.credits, result

    def get_inventory(self, user_id: int):
        return [InventoryEntry(user_id, "iron_ore", self.inventory)] if self.inventory else []


class MiningFormulaTests(unittest.TestCase):
    def test_production_rates(self) -> None:
        self.assertEqual([get_production_rate(level) for level in range(1, 5)], [10, 13, 18, 24])

    def test_storage_capacities(self) -> None:
        self.assertEqual([get_storage_capacity(level) for level in range(1, 5)], [100, 150, 225, 337])

    def test_upgrade_costs(self) -> None:
        self.assertEqual([get_upgrade_cost("storage", level) for level in range(1, 4)], [250, 450, 810])
        self.assertEqual(get_upgrade_cost("production", 1), 400)
        self.assertEqual(get_upgrade_cost("quality", 1), 500)

    def test_upgrade_aliases(self) -> None:
        for alias in ("storage", "stockage"):
            self.assertEqual(resolve_upgrade_type(alias), "storage")
        for alias in ("quality", "qualite", "qualité"):
            self.assertEqual(resolve_upgrade_type(alias), "quality")
        self.assertIsNone(resolve_upgrade_type("camion"))


class MiningServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = MiningRepository()
        self.service = SupabaseIndustrialEconomyService(self.repository)

    async def test_creation_is_idempotent(self) -> None:
        first, second = await asyncio.gather(
            self.service.get_or_create_mine(123),
            self.service.get_or_create_mine(123),
        )
        self.assertEqual(first.company_id, second.company_id)

    async def test_production_after_one_hour(self) -> None:
        await self.service.get_or_create_mine(123)
        self.repository.advance(3600)
        self.assertEqual((await self.service.refresh_mine(123)).stock, 10)

    async def test_production_after_multiple_hours(self) -> None:
        await self.service.get_or_create_mine(123)
        self.repository.advance(8 * 3600)
        self.assertEqual((await self.service.refresh_mine(123)).stock, 80)

    async def test_capacity_and_lost_full_production(self) -> None:
        await self.service.get_or_create_mine(123)
        self.repository.advance(20 * 3600)
        self.assertEqual((await self.service.refresh_mine(123)).stock, 100)
        await self.service.collect_mine(123)
        self.assertEqual((await self.service.refresh_mine(123)).stock, 0)

    async def test_empty_collection(self) -> None:
        result = await self.service.collect_mine(123)
        self.assertEqual(result.collected_quantity, 0)
        self.assertEqual(result.inventory.quantity, 0)

    async def test_collection_moves_stock_to_inventory(self) -> None:
        await self.service.get_or_create_mine(123)
        self.repository.advance(3 * 3600)
        result = await self.service.collect_mine(123)
        self.assertEqual(result.collected_quantity, 30)
        self.assertEqual(result.inventory.quantity, 30)
        self.assertEqual(result.mine.stock, 0)
        self.assertEqual((await self.service.get_inventory(123))[0].quantity, 30)

    async def test_double_collection_does_not_duplicate(self) -> None:
        await self.service.get_or_create_mine(123)
        self.repository.advance(3600)
        results = await asyncio.gather(
            self.service.collect_mine(123),
            self.service.collect_mine(123),
        )
        self.assertEqual(sum(result.collected_quantity for result in results), 10)
        self.assertEqual(self.repository.inventory, 10)

    async def test_insufficient_funds(self) -> None:
        with self.assertRaises(InsufficientIndustrialFundsError) as raised:
            await self.service.upgrade_mine(123, "storage")
        self.assertEqual((raised.exception.cost, raised.exception.balance), (250, 0))

    async def test_successful_upgrade_is_paid_once(self) -> None:
        self.repository.credits = 1000
        result = await self.service.upgrade_mine(123, "production")
        self.assertEqual((result.previous_level, result.new_level), (1, 2))
        self.assertEqual(result.cost, 400)
        self.assertEqual(self.repository.credits, 600)

    async def test_concurrent_upgrades_are_serialized(self) -> None:
        self.repository.credits = 2000
        results = await asyncio.gather(
            self.service.upgrade_mine(123, "production"),
            self.service.upgrade_mine(123, "production"),
        )
        self.assertEqual(sorted(result.new_level for result in results), [2, 3])
        self.assertEqual(self.repository.credits, 2000 - 400 - 720)

    async def test_max_level(self) -> None:
        await self.service.get_or_create_mine(123)
        self.repository.mine = replace(self.repository.mine, storage_level=20)
        with self.assertRaises(MineUpgradeMaxLevelError):
            await self.service.upgrade_mine(123, "storage")

    async def test_mine_company_user_coherence(self) -> None:
        mine = await self.service.get_or_create_mine(123)
        self.assertEqual(self.repository.job, self.repository.company_job)
        self.assertEqual(mine.owner_discord_user_id, 123)


class MineCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_miner_is_refused_without_creating_mine(self) -> None:
        repository = MiningRepository(job="merchant")
        router = build_economy_router(SupabaseIndustrialEconomyService(repository))
        message = FakeMessage("?mine")
        await router.handle(message)
        self.assertIn("réservée aux Mineurs", message.channel.sent[0][0])
        self.assertIsNone(repository.mine)

    async def test_invalid_action_and_upgrade(self) -> None:
        router = build_economy_router(SupabaseIndustrialEconomyService(MiningRepository()))
        invalid_action = FakeMessage("?mine azerty")
        await router.handle(invalid_action)
        self.assertIn("Syntaxes", invalid_action.channel.sent[0][0])
        invalid_upgrade = FakeMessage("?mine upgrade camion")
        await router.handle(invalid_upgrade)
        self.assertIn("Amélioration invalide", invalid_upgrade.channel.sent[0][0])


class MiningMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = (PROJECT_ROOT / "supabase" / "025_industrial_mining.sql").read_text(encoding="utf-8").casefold()

    def test_schema_and_atomic_guards(self) -> None:
        for fragment in (
            "create table if not exists industrial_mines",
            "create table if not exists industrial_inventory",
            "production_progress bigint",
            "pg_advisory_xact_lock",
            "for update",
            "on conflict (owner_discord_user_id) do nothing",
            "on conflict on constraint industrial_inventory_pkey do update",
            "credits = credits - calculated_cost",
            "alter table industrial_mines enable row level security",
            "alter table industrial_inventory enable row level security",
            "security invoker",
            "set search_path = ''",
            "from public, anon, authenticated",
        ):
            self.assertIn(fragment, self.sql)

    def test_rpc_security_and_no_arbitrary_balance(self) -> None:
        self.assertNotIn("security definer", self.sql)
        self.assertNotIn("p_new_balance", self.sql)
        self.assertNotIn("p_upgrade_cost", self.sql)
        for function in (
            "get_or_create_and_refresh_industrial_mine(bigint)",
            "collect_industrial_mine(bigint)",
            "upgrade_industrial_mine(bigint, text)",
        ):
            self.assertIn(f"grant execute on function public.{function} to service_role", self.sql)


if __name__ == "__main__":
    unittest.main()
