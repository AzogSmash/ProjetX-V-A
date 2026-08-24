import asyncio
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.commands.merchant import parse_discord_user_id
from economy_v2.merchant_config import (
    MAX_MERCHANT_UPGRADE_LEVEL, get_merchant_upgrade_cost,
    get_trip_duration_seconds, get_truck_capacity, get_warehouse_capacity,
    resolve_merchant_upgrade,
)
from economy_v2.models import (
    IndustrialTransport, InventoryEntry, Merchant, MerchantTransportResult,
    MerchantUpgradeResult,
)
from economy_v2.services import (
    InsufficientIndustrialFundsError, MerchantAccessDeniedError,
    MerchantTransportError, MerchantUpgradeMaxLevelError,
    SupabaseIndustrialEconomyService,
)


ROOT = Path(__file__).resolve().parents[1]


class MerchantRepository:
    def __init__(self, job="merchant", credits=0):
        self.job = job
        self.company_job = job
        self.credits = credits
        self.inventory = {(10, "iron_ore"): 250}
        self.receiver_inventory = 0
        self.now = 0
        self.merchant = None
        self.transports = []
        self.upgrade_requests = {}
        self.transport_requests = {}
        self.lock = threading.RLock()

    def _refresh(self):
        if self.job != "merchant": return "not_merchant", self.job, None
        if self.company_job != "merchant": return "no_merchant_company", self.job, None
        if self.merchant is None:
            self.merchant = Merchant(10, 100, "Logistique Nord", 1, 1, 1, 1, 0)
        refreshed = []
        for transport in self.transports:
            if transport.status == "in_transit" and int(transport.arrival_at) <= self.now:
                self.receiver_inventory += transport.quantity
                transport = replace(transport, status="delivered")
            refreshed.append(transport)
        self.transports = refreshed
        active = sum(t.status == "in_transit" for t in self.transports)
        self.merchant = replace(self.merchant, active_transports=active)
        return "ok", self.job, self.merchant

    def get_or_create_merchant(self, user_id):
        with self.lock: return self._refresh()

    def upgrade_merchant(self, user_id, upgrade_type, request_id):
        with self.lock:
            status, job, merchant = self._refresh()
            if status != "ok": return status, job, None, None, None
            if request_id in self.upgrade_requests:
                return "duplicate", job, self.upgrade_requests[request_id].cost, self.upgrade_requests[request_id].balance, replace(self.upgrade_requests[request_id], duplicate_request=True)
            field = "truck_count" if upgrade_type == "trucks" else f"truck_{upgrade_type}_level" if upgrade_type in {"capacity", "speed"} else "warehouse_level"
            old = getattr(merchant, field)
            if old >= MAX_MERCHANT_UPGRADE_LEVEL:
                return "max_level", job, None, self.credits, None
            cost = get_merchant_upgrade_cost(upgrade_type, old)
            if self.credits < cost: return "insufficient_funds", job, cost, self.credits, None
            self.credits -= cost
            self.merchant = replace(merchant, **{field: old + 1})
            result = MerchantUpgradeResult(self.merchant, upgrade_type, old, old + 1, cost, self.credits)
            self.upgrade_requests[request_id] = result
            return "ok", job, cost, self.credits, result

    def start_transport(self, user_id, receiver_id, resource, quantity, request_id):
        with self.lock:
            status, job, merchant = self._refresh()
            if status != "ok": return status, job, None, None
            if request_id in self.transport_requests:
                transport = self.transport_requests[request_id]
                return "duplicate", job, None, MerchantTransportResult(transport, True)
            if receiver_id != 20: return "invalid_receiver", job, None, None
            capacity = get_truck_capacity(merchant.truck_capacity_level)
            if quantity > capacity: return "capacity_exceeded", job, capacity, None
            used = {t.truck_slot for t in self.transports if t.status == "in_transit"}
            slot = next((i for i in range(1, merchant.truck_count + 1) if i not in used), None)
            if slot is None: return "no_truck_available", job, None, None
            available = self.inventory.get((user_id, resource), 0)
            if available < quantity: return "insufficient_inventory", job, available, None
            self.inventory[(user_id, resource)] = available - quantity
            duration = get_trip_duration_seconds(merchant.truck_speed_level)
            transport = IndustrialTransport(len(self.transports) + 1, 100, 200, "Forge Sud", user_id, resource, quantity, str(self.now), str(self.now + duration), "in_transit", slot)
            self.transports.append(transport); self.transport_requests[request_id] = transport
            self.merchant = replace(self.merchant, active_transports=merchant.active_transports + 1)
            return "ok", job, available - quantity, MerchantTransportResult(transport)

    def get_merchant_transports(self, user_id):
        with self.lock:
            status, job, _ = self._refresh()
            return status, job, list(reversed(self.transports))

    def get_inventory(self, user_id):
        return [InventoryEntry(user_id, resource, quantity) for (owner, resource), quantity in self.inventory.items() if owner == user_id]


class FakeChannel:
    def __init__(self): self.sent = []
    async def send(self, content=None, **kwargs): self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, content, user_id=10, message_id=1):
        self.content, self.id = content, message_id
        self.author = SimpleNamespace(id=user_id, mention=f"<@{user_id}>")
        self.channel = FakeChannel()


class MerchantFormulaTests(unittest.TestCase):
    def test_capacity_formula(self):
        self.assertEqual([get_truck_capacity(i) for i in range(1, 5)], [100, 150, 225, 337])

    def test_speed_formula_and_floor(self):
        self.assertEqual([get_trip_duration_seconds(i) for i in range(1, 4)], [3600, 3240, 2916])
        self.assertEqual(get_trip_duration_seconds(20), 900)

    def test_warehouse_and_cost_formulas(self):
        self.assertEqual([get_warehouse_capacity(i) for i in range(1, 4)], [1000, 1500, 2250])
        self.assertEqual(get_merchant_upgrade_cost("trucks", 1), 1000)
        self.assertEqual(get_merchant_upgrade_cost("capacity", 2), 1080)

    def test_upgrade_aliases(self):
        self.assertEqual(resolve_merchant_upgrade("camions"), "trucks")
        self.assertEqual(resolve_merchant_upgrade("capacité"), "capacity")
        self.assertEqual(resolve_merchant_upgrade("vitesse"), "speed")
        self.assertEqual(resolve_merchant_upgrade("entrepôt"), "warehouse")
        self.assertIsNone(resolve_merchant_upgrade("forge"))

    def test_discord_user_id_parser(self):
        self.assertEqual(parse_discord_user_id("<@20>"), 20)
        self.assertEqual(parse_discord_user_id("<@!20>"), 20)
        self.assertEqual(parse_discord_user_id("20"), 20)
        self.assertIsNone(parse_discord_user_id("@forgeron"))


class MerchantServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MerchantRepository()
        self.service = SupabaseIndustrialEconomyService(self.repo)

    async def test_profile_creation_is_idempotent(self):
        first, second = await asyncio.gather(
            self.service.get_or_create_merchant(10), self.service.get_or_create_merchant(10))
        self.assertEqual(first.company_id, second.company_id)

    async def test_non_merchant_is_refused_without_profile(self):
        self.repo.job = "miner"
        with self.assertRaises(MerchantAccessDeniedError):
            await self.service.get_or_create_merchant(10)
        self.assertIsNone(self.repo.merchant)

    async def test_upgrade_is_atomic_and_idempotent(self):
        self.repo.credits = 2000
        first = await self.service.upgrade_merchant(10, "capacity", "request")
        second = await self.service.upgrade_merchant(10, "capacity", "request")
        self.assertEqual((first.new_level, second.new_level), (2, 2))
        self.assertEqual(self.repo.credits, 1400)
        self.assertTrue(second.duplicate_request)

    async def test_concurrent_upgrades_are_serialized(self):
        self.repo.credits = 3000
        results = await asyncio.gather(
            self.service.upgrade_merchant(10, "speed", "one"),
            self.service.upgrade_merchant(10, "speed", "two"))
        self.assertEqual(sorted(r.new_level for r in results), [2, 3])
        self.assertEqual(self.repo.credits, 3000 - 800 - 1440)

    async def test_insufficient_funds_and_max_level(self):
        with self.assertRaises(InsufficientIndustrialFundsError):
            await self.service.upgrade_merchant(10, "trucks", "poor")
        await self.service.get_or_create_merchant(10)
        self.repo.merchant = replace(self.repo.merchant, truck_count=20)
        with self.assertRaises(MerchantUpgradeMaxLevelError):
            await self.service.upgrade_merchant(10, "trucks", "max")

    async def test_transport_reserves_inventory_and_truck(self):
        result = await self.service.start_transport(10, 20, "iron_ore", 80, "trip")
        self.assertEqual(self.repo.inventory[(10, "iron_ore")], 170)
        self.assertEqual((result.transport.truck_slot, result.transport.status), (1, "in_transit"))
        with self.assertRaises(MerchantTransportError) as raised:
            await self.service.start_transport(10, 20, "iron_ore", 10, "second")
        self.assertEqual(raised.exception.reason, "no_truck_available")

    async def test_transport_limits_and_receiver(self):
        for receiver, quantity, reason in ((99, 10, "invalid_receiver"), (20, 101, "capacity_exceeded")):
            with self.assertRaises(MerchantTransportError) as raised:
                await self.service.start_transport(10, receiver, "iron_ore", quantity, str(reason))
            self.assertEqual(raised.exception.reason, reason)
        self.repo.inventory[(10, "iron_ore")] = 5
        with self.assertRaises(MerchantTransportError) as raised:
            await self.service.start_transport(10, 20, "iron_ore", 10, "stock")
        self.assertEqual(raised.exception.reason, "insufficient_inventory")

    async def test_double_transport_request_does_not_duplicate(self):
        results = await asyncio.gather(*[
            self.service.start_transport(10, 20, "iron_ore", 50, "same") for _ in range(2)])
        self.assertEqual(len(self.repo.transports), 1)
        self.assertEqual(self.repo.inventory[(10, "iron_ore")], 200)
        self.assertTrue(any(r.duplicate_request for r in results))

    async def test_lazy_delivery_is_exactly_once_and_frees_truck(self):
        await self.service.start_transport(10, 20, "iron_ore", 50, "trip")
        self.repo.now = 3600
        await asyncio.gather(*[
            self.service.get_merchant_transports(10) for _ in range(2)])
        self.assertEqual(self.repo.receiver_inventory, 50)
        self.assertEqual((await self.service.get_or_create_merchant(10)).active_transports, 0)
        await self.service.start_transport(10, 20, "iron_ore", 20, "next")
        self.assertEqual(len(self.repo.transports), 2)


class MerchantCommandAndSqlTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_and_commands(self):
        repo = MerchantRepository(); service = SupabaseIndustrialEconomyService(repo)
        router = build_economy_router(service)
        self.assertIn("merchant", router.command_names)
        for index, content in enumerate(("?merchant", "?merchant inventory", "?merchant transport <@20> iron_ore 50", "?merchant transports"), 1):
            message = FakeMessage(content, message_id=index)
            await router.handle(message)
            self.assertTrue(message.channel.sent)
            self.assertNotIn("Une erreur est survenue", message.channel.sent[0][0] or "")

    async def test_bad_transport_parsing(self):
        router = build_economy_router(SupabaseIndustrialEconomyService(MerchantRepository()))
        message = FakeMessage("?merchant transport personne iron_ore dix")
        await router.handle(message)
        self.assertIn("mention", message.channel.sent[0][0])

    def test_sql_security_concurrency_and_lazy_delivery(self):
        sql = (ROOT / "supabase" / "027_industrial_merchant.sql").read_text(encoding="utf-8").casefold()
        for fragment in (
            "security invoker", "set search_path = ''", "enable row level security",
            "from public, anon, authenticated", "to service_role",
            "pg_advisory_xact_lock", "for update", "request_id text not null unique",
            "industrial_transports_active_truck_idx", "where status = 'in_transit'",
            "arrival_at <= v_current_time", "quantity = industrial_inventory.quantity + excluded.quantity",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("security definer", sql)

    def test_no_legacy_economy_reference(self):
        paths = (ROOT / "economy_v2" / "commands" / "merchant.py", ROOT / "economy_v2" / "merchant_config.py", ROOT / "supabase" / "027_industrial_merchant.sql")
        content = "\n".join(path.read_text(encoding="utf-8").casefold() for path in paths)
        for legacy in ("data.json", "casino", "coins"):
            self.assertNotIn(legacy, content)


if __name__ == "__main__":
    unittest.main()
