import asyncio
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.market_config import validate_market_amounts
from economy_v2.models import MarketOrder, MarketOrderResult, MarketSummary
from economy_v2.resources import RESOURCES, get_resource
from economy_v2.services import (
    MarketAccessDeniedError, MarketInsufficientAssetsError, MarketOrderClosedError,
    MarketOrderLimitError, SQLiteIndustrialEconomyService,
)


ROOT = Path(__file__).resolve().parents[1]


class MarketRepository:
    def __init__(self) -> None:
        self.jobs = {1: "miner", 2: "merchant"}
        self.inventory = {(1, "iron_ore"): 100}
        self.credits = {1: 0, 2: 1000}
        self.orders = {}
        self.requests = {}
        self.next_id = 1
        self.trades = []
        self.lock = threading.RLock()

    def create_market_order(self, user, side, resource, quantity, price, request_id):
        with self.lock:
            if request_id in self.requests:
                order = self.orders[self.requests[request_id]]
                return "duplicate", MarketOrderResult(order, order.original_quantity - order.remaining_quantity, True), None
            required = "miner" if side == "sell" else "merchant"
            if self.jobs.get(user) != required:
                return f"not_{required}", None, None
            if sum(o.owner_discord_user_id == user and o.status == "open" for o in self.orders.values()) >= 20:
                return "order_limit", None, None
            total = quantity * price
            if side == "sell":
                available = self.inventory.get((user, resource), 0)
                if available < quantity:
                    return "insufficient_inventory", None, available
                self.inventory[(user, resource)] = available - quantity
            else:
                available = self.credits.get(user, 0)
                if available < total:
                    return "insufficient_funds", None, available
                self.credits[user] = available - total
            order = MarketOrder(self.next_id, user, side, resource, quantity, quantity, price, "open", str(self.next_id))
            self.orders[order.id] = order
            self.requests[request_id] = order.id
            self.next_id += 1
            opposite = "sell" if side == "buy" else "buy"
            candidates = [o for o in self.orders.values() if o.side == opposite and o.status == "open" and o.owner_discord_user_id != user and ((side == "buy" and o.unit_price <= price) or (side == "sell" and o.unit_price >= price))]
            candidates.sort(key=lambda o: ((o.unit_price if side == "buy" else -o.unit_price), int(o.created_at), o.id))
            for other in candidates:
                order = self.orders[order.id]
                if not order.remaining_quantity:
                    break
                amount = min(order.remaining_quantity, other.remaining_quantity)
                buy = order if side == "buy" else other
                sell = other if side == "buy" else order
                trade_price = other.unit_price
                self.credits[sell.owner_discord_user_id] += amount * trade_price
                self.credits[buy.owner_discord_user_id] += amount * (buy.unit_price - trade_price)
                self.inventory[(buy.owner_discord_user_id, resource)] = self.inventory.get((buy.owner_discord_user_id, resource), 0) + amount
                for current in (order, other):
                    remaining = current.remaining_quantity - amount
                    self.orders[current.id] = replace(current, remaining_quantity=remaining, status="open" if remaining else "filled")
                self.trades.append((sell.owner_discord_user_id, buy.owner_discord_user_id, amount, trade_price))
            order = self.orders[order.id]
            return "ok", MarketOrderResult(order, quantity - order.remaining_quantity), None

    def cancel_market_order(self, user, order_id):
        with self.lock:
            order = self.orders.get(order_id)
            if not order or order.owner_discord_user_id != user:
                return "not_found", None
            if order.status != "open":
                return "already_closed", None
            if order.side == "sell":
                key = (user, order.resource_type)
                self.inventory[key] = self.inventory.get(key, 0) + order.remaining_quantity
            else:
                self.credits[user] += order.remaining_quantity * order.unit_price
            order = replace(order, remaining_quantity=0, status="cancelled")
            self.orders[order_id] = order
            return "ok", order

    def get_market_orders(self, user):
        return [o for o in self.orders.values() if o.owner_discord_user_id == user and o.status == "open"]

    def get_market_summary(self, resource, depth):
        sells = sorted((o for o in self.orders.values() if o.side == "sell" and o.status == "open"), key=lambda o: (o.unit_price, int(o.created_at)))[:depth]
        buys = sorted((o for o in self.orders.values() if o.side == "buy" and o.status == "open"), key=lambda o: (-o.unit_price, int(o.created_at)))[:depth]
        volume = sum(t[2] for t in self.trades)
        prices = [t[3] for t in self.trades]
        average = sum(t[2] * t[3] for t in self.trades) / volume if volume else None
        return MarketSummary(resource, average, min(prices) if prices else None, max(prices) if prices else None, volume, tuple(sells), tuple(buys))


class FakeChannel:
    def __init__(self): self.sent = []
    async def send(self, content=None, **kwargs): self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, content, user_id=1, message_id=99):
        self.content, self.id = content, message_id
        self.author = SimpleNamespace(id=user_id, mention=f"<@{user_id}>")
        self.channel = FakeChannel()


class MarketConfigTests(unittest.TestCase):
    def test_resources_are_centralized(self):
        self.assertEqual(RESOURCES["iron_ore"].label, "Minerai de fer")
        self.assertEqual(RESOURCES["iron_ingot"].label, "Lingot de fer")
        self.assertFalse(RESOURCES["iron_ingot"].market_enabled)

    def test_resource_lookup_and_limits(self):
        self.assertEqual(get_resource("IRON_ORE").resource_type, "iron_ore")
        validate_market_amounts(1, 1)
        validate_market_amounts(1_000_000, 1_000_000)
        for args in ((0, 1), (1, 0), (1_000_001, 1), (1, 1_000_001)):
            with self.assertRaises(ValueError): validate_market_amounts(*args)


class MarketServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MarketRepository()
        self.service = SQLiteIndustrialEconomyService(self.repo)

    async def test_wrong_jobs_are_refused_without_escrow(self):
        with self.assertRaises(MarketAccessDeniedError):
            await self.service.create_market_order(2, "sell", "iron_ore", 10, 8, "a")
        with self.assertRaises(MarketAccessDeniedError):
            await self.service.create_market_order(1, "buy", "iron_ore", 10, 8, "b")
        self.assertEqual(self.repo.inventory[(1, "iron_ore")], 100)
        self.assertEqual(self.repo.credits[2], 1000)

    async def test_sell_escrows_inventory_and_cancel_refunds(self):
        result = await self.service.create_market_order(1, "sell", "iron_ore", 50, 8, "sell")
        self.assertEqual(self.repo.inventory[(1, "iron_ore")], 50)
        await self.service.cancel_market_order(1, result.order.id)
        self.assertEqual(self.repo.inventory[(1, "iron_ore")], 100)

    async def test_buy_escrows_credits_and_cancel_refunds(self):
        result = await self.service.create_market_order(2, "buy", "iron_ore", 100, 7, "buy")
        self.assertEqual(self.repo.credits[2], 300)
        await self.service.cancel_market_order(2, result.order.id)
        self.assertEqual(self.repo.credits[2], 1000)

    async def test_matching_conserves_assets_at_maker_price(self):
        await self.service.create_market_order(1, "sell", "iron_ore", 50, 8, "sell")
        result = await self.service.create_market_order(2, "buy", "iron_ore", 50, 10, "buy")
        self.assertEqual(result.filled_quantity, 50)
        self.assertEqual(self.repo.inventory[(2, "iron_ore")], 50)
        self.assertEqual(self.repo.credits, {1: 400, 2: 600})

    async def test_price_then_time_priority(self):
        self.repo.jobs[3] = "miner"; self.repo.inventory[(3, "iron_ore")] = 10; self.repo.credits[3] = 0
        await self.service.create_market_order(1, "sell", "iron_ore", 10, 9, "late-price")
        await self.service.create_market_order(3, "sell", "iron_ore", 10, 8, "best-price")
        await self.service.create_market_order(2, "buy", "iron_ore", 10, 10, "buyer")
        self.assertEqual(self.repo.trades[0][0], 3)

    async def test_idempotent_request_and_concurrent_buy(self):
        await self.service.create_market_order(1, "sell", "iron_ore", 100, 8, "sell")
        results = await asyncio.gather(*[
            self.service.create_market_order(2, "buy", "iron_ore", 50, 8, "same-request")
            for _ in range(2)
        ])
        self.assertEqual(sum(t[2] for t in self.repo.trades), 50)
        self.assertTrue(any(result.duplicate_request for result in results))

    async def test_insufficient_assets_and_closed_cancel(self):
        with self.assertRaises(MarketInsufficientAssetsError):
            await self.service.create_market_order(1, "sell", "iron_ore", 101, 8, "too-many")
        result = await self.service.create_market_order(1, "sell", "iron_ore", 1, 8, "one")
        await self.service.cancel_market_order(1, result.order.id)
        with self.assertRaises(MarketOrderClosedError):
            await self.service.cancel_market_order(1, result.order.id)

    async def test_open_order_limit(self):
        for index in range(20):
            await self.service.create_market_order(1, "sell", "iron_ore", 1, index + 1, f"o{index}")
        with self.assertRaises(MarketOrderLimitError):
            await self.service.create_market_order(1, "sell", "iron_ore", 1, 30, "overflow")


class MarketCommandAndSqlTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_parsing_and_registry(self):
        repo = MarketRepository(); router = build_economy_router(SQLiteIndustrialEconomyService(repo))
        self.assertIn("market", router.command_names)
        message = FakeMessage("?market sell iron_ore 10 8")
        self.assertTrue(await router.handle(message))
        self.assertEqual(repo.inventory[(1, "iron_ore")], 90)
        invalid = FakeMessage("?market sell iron_ore dix 8", message_id=100)
        await router.handle(invalid)
        self.assertIn("entiers", invalid.channel.sent[0][0])

    async def test_market_display_and_orders(self):
        repo = MarketRepository(); router = build_economy_router(SQLiteIndustrialEconomyService(repo))
        for content in ("?market", "?market orders"):
            message = FakeMessage(content)
            await router.handle(message)
            self.assertTrue(message.channel.sent)

    def test_sql_security_atomicity_and_constraints(self):
        sql = (ROOT / "supabase" / "026_industrial_market.sql").read_text(encoding="utf-8").casefold()
        for fragment in (
            "security invoker", "set search_path = ''", "enable row level security",
            "revoke all on table", "from public, anon, authenticated",
            "pg_advisory_xact_lock", "for update", "request_id text not null unique",
            "escrow_quantity", "escrow_credits", "industrial_market_trades",
            "grant execute on function", "to service_role",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("security definer", sql)

    def test_new_code_does_not_reference_legacy_currency(self):
        paths = [ROOT / "economy_v2" / "commands" / "market.py", ROOT / "economy_v2" / "market_config.py", ROOT / "economy_v2" / "resources.py"]
        joined = "\n".join(path.read_text(encoding="utf-8").casefold() for path in paths)
        for legacy in ("data.json", "casino", "coins"):
            self.assertNotIn(legacy, joined)


if __name__ == "__main__":
    unittest.main()
