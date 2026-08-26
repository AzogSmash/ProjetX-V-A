import asyncio
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.forge_config import (
    MAX_FORGE_UPGRADE_LEVEL, get_forge_count, get_forge_duration_seconds,
    get_forge_output_quantity, get_forge_rate, get_forge_storage_capacity,
    get_forge_upgrade_cost, resolve_forge_upgrade,
)
from economy_v2.models import (
    Blacksmith, ForgeCollectionResult, ForgeJob, ForgeProcessResult,
    ForgeUpgradeResult, InventoryEntry,
)
from economy_v2.services import (
    BlacksmithAccessDeniedError, ForgeProcessError, ForgeUpgradeMaxLevelError,
    InsufficientIndustrialFundsError, SQLiteIndustrialEconomyService,
)


ROOT = Path(__file__).resolve().parents[1]


class ForgeRepository:
    def __init__(self, job="blacksmith", credits=0):
        self.job = job; self.company_job = job; self.credits = credits
        self.now = 0; self.blacksmith = None; self.jobs = []
        self.inventory = {(30, "iron_ore"): 300, (30, "iron_ingot"): 0}
        self.inbound = [{"quantity": 50, "arrival": 100, "status": "in_transit"}]
        self.process_requests = {}; self.collection_requests = {}; self.upgrade_requests = {}
        self.lock = threading.RLock()

    def _refresh(self):
        if self.job != "blacksmith": return "not_blacksmith", self.job, None
        if self.company_job != "blacksmith": return "no_blacksmith_company", self.job, None
        if self.blacksmith is None:
            self.blacksmith = Blacksmith(30, 300, "Forges du Nord", 1, 1, 1, 1, 0, 0, 0)
        for transport in self.inbound:
            if transport["status"] == "in_transit" and transport["arrival"] <= self.now:
                self.inventory[(30, "iron_ore")] += transport["quantity"]
                transport["status"] = "delivered"
        self.jobs = [replace(j, status="completed") if j.status == "processing" and int(j.finishes_at) <= self.now else j for j in self.jobs]
        active = sum(j.status == "processing" for j in self.jobs)
        completed = sum(j.status == "completed" for j in self.jobs)
        reserved = sum(j.output_quantity for j in self.jobs if j.status in {"processing", "completed"})
        self.blacksmith = replace(self.blacksmith, active_jobs=active, completed_jobs=completed, reserved_output=reserved)
        return "ok", self.job, self.blacksmith

    def get_or_create_blacksmith(self, user_id):
        with self.lock: return self._refresh()

    def start_forge_job(self, user_id, resource, quantity, request_id):
        with self.lock:
            status, job, blacksmith = self._refresh()
            if status != "ok": return status, job, None, None
            if request_id in self.process_requests:
                old = self.process_requests[request_id]
                return "duplicate", job, self.inventory[(user_id, resource)], ForgeProcessResult(old, self.inventory[(user_id, resource)], True)
            used = {j.forge_slot for j in self.jobs if j.status == "processing"}
            slot = next((i for i in range(1, get_forge_count(blacksmith.forge_level) + 1) if i not in used), None)
            if slot is None: return "no_forge_available", job, None, None
            free_storage = get_forge_storage_capacity(blacksmith.storage_level) - blacksmith.reserved_output
            if quantity > free_storage: return "storage_full", job, free_storage, None
            available = self.inventory.get((user_id, resource), 0)
            if available < quantity: return "insufficient_inventory", job, available, None
            self.inventory[(user_id, resource)] = available - quantity
            duration = get_forge_duration_seconds(quantity, blacksmith.speed_level)
            forge_job = ForgeJob(len(self.jobs) + 1, user_id, 300, slot, "iron_ore", "iron_ingot", quantity, quantity, str(self.now), str(self.now + duration), "processing")
            self.jobs.append(forge_job); self.process_requests[request_id] = forge_job
            self._refresh()
            return "ok", job, available - quantity, ForgeProcessResult(forge_job, available - quantity)

    def collect_forge_jobs(self, user_id, request_id):
        with self.lock:
            status, job, _ = self._refresh()
            if status != "ok": return status, job, None
            if request_id in self.collection_requests:
                return "duplicate", job, replace(self.collection_requests[request_id], duplicate_request=True)
            collected = sum(j.output_quantity for j in self.jobs if j.status == "completed")
            self.inventory[(user_id, "iron_ingot")] += collected
            self.jobs = [replace(j, status="collected") if j.status == "completed" else j for j in self.jobs]
            result = ForgeCollectionResult(collected, self.inventory[(user_id, "iron_ingot")])
            self.collection_requests[request_id] = result; self._refresh()
            return "ok", job, result

    def upgrade_forge(self, user_id, upgrade_type, request_id):
        with self.lock:
            status, job, blacksmith = self._refresh()
            if status != "ok": return status, job, None, None, None
            if request_id in self.upgrade_requests:
                result = replace(self.upgrade_requests[request_id], duplicate_request=True)
                return "duplicate", job, result.cost, result.balance, result
            field = "forge_level" if upgrade_type == "forges" else f"{upgrade_type}_level"
            old = getattr(blacksmith, field)
            if old >= MAX_FORGE_UPGRADE_LEVEL: return "max_level", job, None, self.credits, None
            cost = get_forge_upgrade_cost(upgrade_type, old)
            if self.credits < cost: return "insufficient_funds", job, cost, self.credits, None
            self.credits -= cost; self.blacksmith = replace(blacksmith, **{field: old + 1})
            result = ForgeUpgradeResult(self.blacksmith, upgrade_type, old, old + 1, cost, self.credits)
            self.upgrade_requests[request_id] = result
            return "ok", job, cost, self.credits, result

    def get_forge_jobs(self, user_id):
        with self.lock:
            status, job, _ = self._refresh()
            return status, job, list(reversed(self.jobs))

    def get_inventory(self, user_id):
        return [InventoryEntry(user_id, resource, quantity) for (owner, resource), quantity in self.inventory.items() if owner == user_id]


class FakeChannel:
    def __init__(self): self.sent = []
    async def send(self, content=None, **kwargs): self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, content, user_id=30, message_id=1):
        self.content, self.id = content, message_id
        self.author = SimpleNamespace(id=user_id, mention=f"<@{user_id}>")
        self.channel = FakeChannel()


class ForgeFormulaTests(unittest.TestCase):
    def test_forge_count_and_rate(self):
        self.assertEqual([get_forge_count(i) for i in range(1, 4)], [1, 2, 3])
        self.assertEqual([get_forge_rate(i) for i in range(1, 4)], [10, 13, 18])

    def test_storage_formula(self):
        self.assertEqual([get_forge_storage_capacity(i) for i in range(1, 4)], [500, 750, 1125])

    def test_duration_is_integer_and_deterministic(self):
        self.assertEqual(get_forge_duration_seconds(10, 1), 3600)
        self.assertEqual(get_forge_duration_seconds(1, 2), 277)

    def test_yield_remains_one_to_one(self):
        self.assertEqual(get_forge_output_quantity(100, 1), 100)
        self.assertEqual(get_forge_output_quantity(100, 20), 100)

    def test_upgrade_costs_and_aliases(self):
        self.assertEqual(get_forge_upgrade_cost("forges", 1), 1200)
        self.assertEqual(get_forge_upgrade_cost("speed", 2), 1620)
        self.assertEqual(resolve_forge_upgrade("vitesse"), "speed")
        self.assertEqual(resolve_forge_upgrade("stockage"), "storage")
        self.assertEqual(resolve_forge_upgrade("rendement"), "yield")
        self.assertIsNone(resolve_forge_upgrade("camion"))


class ForgeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = ForgeRepository(); self.service = SQLiteIndustrialEconomyService(self.repo)

    async def test_profile_creation_and_wrong_role(self):
        first, second = await asyncio.gather(self.service.get_or_create_blacksmith(30), self.service.get_or_create_blacksmith(30))
        self.assertEqual(first.company_id, second.company_id)
        self.repo.job = "merchant"; self.repo.blacksmith = None
        with self.assertRaises(BlacksmithAccessDeniedError):
            await self.service.get_or_create_blacksmith(30)

    async def test_forge_receives_arrived_transport_without_merchant(self):
        self.repo.now = 100
        await self.service.get_or_create_blacksmith(30)
        self.assertEqual(self.repo.inventory[(30, "iron_ore")], 350)
        await self.service.get_or_create_blacksmith(30)
        self.assertEqual(self.repo.inventory[(30, "iron_ore")], 350)

    async def test_process_removes_ore_and_is_idempotent(self):
        first, second = await asyncio.gather(*[
            self.service.start_forge_job(30, "iron_ore", 100, "same") for _ in range(2)])
        self.assertEqual(len(self.repo.jobs), 1)
        self.assertEqual(self.repo.inventory[(30, "iron_ore")], 200)
        self.assertTrue(any(result.duplicate_request for result in (first, second)))

    async def test_active_jobs_respect_forge_count(self):
        await self.service.start_forge_job(30, "iron_ore", 10, "first")
        with self.assertRaises(ForgeProcessError) as raised:
            await self.service.start_forge_job(30, "iron_ore", 10, "second")
        self.assertEqual(raised.exception.reason, "no_forge_available")

    async def test_finished_uncollected_job_persists_and_frees_forge(self):
        await self.service.start_forge_job(30, "iron_ore", 10, "first")
        self.repo.now = 3600
        jobs = await self.service.get_forge_jobs(30)
        self.assertEqual(jobs[0].status, "completed")
        await self.service.start_forge_job(30, "iron_ore", 10, "second")
        self.assertEqual(self.repo.jobs[-1].forge_slot, 1)

    async def test_storage_and_inventory_limits(self):
        with self.assertRaises(ForgeProcessError) as raised:
            await self.service.start_forge_job(30, "iron_ore", 501, "storage")
        self.assertEqual(raised.exception.reason, "storage_full")
        self.repo.inventory[(30, "iron_ore")] = 5
        with self.assertRaises(ForgeProcessError) as raised:
            await self.service.start_forge_job(30, "iron_ore", 10, "inventory")
        self.assertEqual(raised.exception.reason, "insufficient_inventory")

    async def test_collection_moves_ingots_exactly_once(self):
        await self.service.start_forge_job(30, "iron_ore", 10, "job")
        self.repo.now = 3600
        results = await asyncio.gather(*[
            self.service.collect_forge_jobs(30, request) for request in ("collect-a", "collect-b")])
        self.assertEqual(sum(result.collected_quantity for result in results), 10)
        self.assertEqual(self.repo.inventory[(30, "iron_ingot")], 10)
        self.assertEqual(self.repo.jobs[0].status, "collected")

    async def test_collection_request_is_idempotent(self):
        await self.service.start_forge_job(30, "iron_ore", 10, "job")
        self.repo.now = 3600
        first = await self.service.collect_forge_jobs(30, "same")
        second = await self.service.collect_forge_jobs(30, "same")
        self.assertEqual((first.collected_quantity, second.collected_quantity), (10, 10))
        self.assertEqual(self.repo.inventory[(30, "iron_ingot")], 10)
        self.assertTrue(second.duplicate_request)

    async def test_upgrade_payment_concurrency_and_idempotence(self):
        self.repo.credits = 5000
        results = await asyncio.gather(
            self.service.upgrade_forge(30, "speed", "one"),
            self.service.upgrade_forge(30, "speed", "two"))
        self.assertEqual(sorted(result.new_level for result in results), [2, 3])
        self.assertEqual(self.repo.credits, 5000 - 900 - 1620)
        duplicate = await self.service.upgrade_forge(30, "speed", "one")
        self.assertTrue(duplicate.duplicate_request)

    async def test_upgrade_insufficient_and_max(self):
        with self.assertRaises(InsufficientIndustrialFundsError):
            await self.service.upgrade_forge(30, "forges", "poor")
        await self.service.get_or_create_blacksmith(30)
        self.repo.blacksmith = replace(self.repo.blacksmith, yield_level=20)
        with self.assertRaises(ForgeUpgradeMaxLevelError):
            await self.service.upgrade_forge(30, "yield", "max")


class ForgeCommandAndSqlTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_and_commands(self):
        repo = ForgeRepository(); router = build_economy_router(SQLiteIndustrialEconomyService(repo))
        self.assertIn("forge", router.command_names)
        commands = ("?forge", "?forge inventory", "?forge process iron_ore 10", "?forge jobs")
        for index, content in enumerate(commands, 1):
            message = FakeMessage(content, message_id=index)
            await router.handle(message)
            self.assertTrue(message.channel.sent)
            self.assertNotIn("Une erreur est survenue", message.channel.sent[0][0] or "")

    async def test_invalid_process_and_upgrade(self):
        router = build_economy_router(SQLiteIndustrialEconomyService(ForgeRepository()))
        for content, expected in (("?forge process copper 10", "iron_ore"), ("?forge upgrade camion", "invalide")):
            message = FakeMessage(content)
            await router.handle(message)
            self.assertIn(expected, message.channel.sent[0][0])

    def test_sql_security_and_concurrency_guards(self):
        sql = (ROOT / "supabase" / "028_industrial_forge.sql").read_text(encoding="utf-8").casefold()
        for fragment in (
            "security invoker", "set search_path = ''", "enable row level security",
            "from public, anon, authenticated", "to service_role", "pg_advisory_xact_lock",
            "for update", "request_id text not null unique", "industrial_forge_jobs_active_slot_idx",
            "receiver_company_id = blacksmith_company_id", "arrival_at <= v_current_time",
            "status = 'collected'", "quantity = industrial_inventory.quantity + excluded.quantity",
            "credits = credits - cost_value",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("security definer", sql)

    def test_no_legacy_economy_reference(self):
        paths = (ROOT / "economy_v2" / "commands" / "forge.py", ROOT / "economy_v2" / "forge_config.py", ROOT / "supabase" / "028_industrial_forge.sql")
        content = "\n".join(path.read_text(encoding="utf-8").casefold() for path in paths)
        for legacy in ("data.json", "casino", "coins"):
            self.assertNotIn(legacy, content)


if __name__ == "__main__":
    unittest.main()
