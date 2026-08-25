import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.commands.next_actions import build_recommendations
from economy_v2.database import connect_database, immediate_transaction
from economy_v2.services import SQLiteIndustrialEconomyService
from economy_v2.sqlite_repository import SQLiteIndustrialEconomyRepository


def base_snapshot(**changes):
    snapshot = {
        "profile_exists": True,
        "job": None,
        "company": None,
        "wallet": 0,
        "mine": None,
        "inventory": {},
        "open_market_orders": 0,
        "best_iron_ore_buy_price": None,
        "active_transports": 0,
        "arrived_transports": 0,
        "merchant": None,
        "forge": None,
        "ready_forge_ingots": 0,
        "processing_forge_jobs": 0,
        "pending_ingot_shipments": 0,
        "pending_ingot_shipment_id": None,
        "available_delivery_missions": 0,
        "delivery_cooldown_until": 0,
        "contracts": {},
        "world_price": 80,
    }
    snapshot.update(changes)
    return snapshot


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, content, user_id=42):
        self.content = content
        self.id = 123
        self.author = SimpleNamespace(id=user_id)
        self.channel = FakeChannel()


class SnapshotService:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.activity_calls = 0
        self.snapshot_calls = 0

    async def record_activity(self, user_id):
        self.activity_calls += 1

    async def get_next_actions_snapshot(self, user_id):
        self.snapshot_calls += 1
        return self.snapshot


class RecommendationTests(unittest.TestCase):
    def test_miner_recommendations_are_prioritized_and_actionable(self):
        recommendations = build_recommendations(base_snapshot(
            job="miner",
            company={"name": "Mine"},
            wallet=620,
            mine={
                "stock": 84, "capacity": 100, "storage_level": 1,
                "production_level": 1, "quality_level": 1,
            },
            inventory={"iron_ore": 84},
            best_iron_ore_buy_price=8,
            available_delivery_missions=1,
            contracts={"iron_ore": 3},
        ))
        commands = [recommendation[3] for recommendation in recommendations]
        self.assertEqual("?mine collect", commands[0])
        self.assertIn("?market sell iron_ore 84 8", commands)
        self.assertIn("?mine upgrade storage", commands)
        self.assertIn("?delivery list", commands)
        self.assertIn("?contracts", commands)

    def test_at_most_six_recommendations_are_returned(self):
        recommendations = build_recommendations(base_snapshot(
            job="blacksmith", company={"name": "Forge"}, wallet=10_000,
            inventory={"iron_ore": 100, "iron_ingot": 50},
            forge={"forge_level": 2, "speed_level": 1,
                   "storage_level": 1, "yield_level": 1},
            ready_forge_ingots=20, pending_ingot_shipments=2,
            available_delivery_missions=3, open_market_orders=4,
            contracts={"iron_ore": 2, "iron_ingot": 2},
        ))
        self.assertLessEqual(len(recommendations), 6)
        self.assertEqual(sorted((r[0] for r in recommendations), reverse=True),
                         [r[0] for r in recommendations])

    def test_banker_gets_current_world_price_recommendation(self):
        recommendations = build_recommendations(base_snapshot(
            job="banker", company={"name": "Bank"},
            inventory={"iron_ingot": 12}, world_price=93,
        ))
        self.assertIn("?bank sell iron_ingot 12", [item[3] for item in recommendations])
        self.assertTrue(any("93 CR" in item[2] for item in recommendations))

    def test_player_without_company_is_guided_to_company_creation(self):
        recommendations = build_recommendations(base_snapshot())
        self.assertEqual("?company create <métier> <nom>", recommendations[0][3])


class NextActionsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_aliases_work_without_activity_tracking(self):
        service = SnapshotService(base_snapshot())
        router = build_economy_router(service)
        self.assertTrue({"next", "go", "progress"}.issubset(router.command_names))
        for content in ("?next", "?go", "?progress"):
            message = FakeMessage(content)
            self.assertTrue(await router.handle(message))
            self.assertEqual("🧭 Actions disponibles pour progresser",
                             message.channel.sent[0][1]["embed"].title)
        self.assertEqual(3, service.snapshot_calls)
        self.assertEqual(0, service.activity_calls)

    async def test_arguments_are_rejected_without_snapshot_access(self):
        service = SnapshotService(base_snapshot())
        message = FakeMessage("?next now")
        await build_economy_router(service).handle(message)
        self.assertEqual("Syntaxe : `?next`.", message.channel.sent[0][0])
        self.assertEqual(0, service.snapshot_calls)


class ReadOnlySnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "industrial.db"
        self.repository = SQLiteIndustrialEconomyRepository(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def database_dump(self):
        with closing(connect_database(self.database_path)) as connection:
            return "\n".join(connection.iterdump())

    async def test_next_command_does_not_mutate_any_sqlite_state(self):
        self.repository.create_first_company(42, "Mine lecture seule", "miner")
        self.repository.get_or_create_and_refresh_mine(42)
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_users SET credits=620 WHERE discord_user_id=42"
            )
            connection.execute(
                "UPDATE industrial_mines SET stock=84,last_production_at=? "
                "WHERE owner_discord_user_id=42",
                (int(time.time()),),
            )
        before = self.database_dump()
        message = FakeMessage("?next")
        router = build_economy_router(SQLiteIndustrialEconomyService(self.repository))
        await router.handle(message)
        after = self.database_dump()
        self.assertEqual(before, after)
        self.assertIn("?mine collect", message.channel.sent[0][1]["embed"].fields[0].value)

    async def test_unknown_player_is_not_created(self):
        before = self.database_dump()
        message = FakeMessage("?progress", user_id=999)
        await build_economy_router(
            SQLiteIndustrialEconomyService(self.repository)
        ).handle(message)
        self.assertEqual(before, self.database_dump())
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(0, connection.execute(
                "SELECT count(*) FROM industrial_users WHERE discord_user_id=999"
            ).fetchone()[0])

    async def test_snapshot_projects_mine_and_respects_delivery_cooldown_read_only(self):
        self.repository.create_first_company(42, "Mine projection", "miner")
        self.repository.get_or_create_and_refresh_mine(42)
        now = int(time.time())
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_mines SET stock=10,production_progress=0,"
                "last_production_at=? WHERE owner_discord_user_id=42",
                (now - 3600,),
            )
            connection.execute(
                "INSERT INTO industrial_delivery_profiles("
                "discord_user_id,delivery_cooldown_until) VALUES(?,?)",
                (42, now + 600),
            )
        before = self.database_dump()
        snapshot = await SQLiteIndustrialEconomyService(
            self.repository
        ).get_next_actions_snapshot(42)
        self.assertEqual(20, snapshot["mine"]["stock"])
        self.assertGreater(snapshot["delivery_cooldown_until"], now)
        self.assertEqual(0, snapshot["available_delivery_missions"])
        self.assertEqual(before, self.database_dump())


if __name__ == "__main__":
    unittest.main()
