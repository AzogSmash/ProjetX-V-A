import asyncio
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from economy_v2.database import (
    connect_database,
    get_database_path,
    immediate_transaction,
    initialize_database_sync,
)
from economy_v2.sqlite_repository import SQLiteIndustrialEconomyRepository
from tools.backup_industrial_db import backup_database


class SQLiteIndustrialRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "industrial.db"
        self.repository = SQLiteIndustrialEconomyRepository(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def company(self, user_id, job):
        status, company = self.repository.create_first_company(
            user_id, f"Company {user_id}", job
        )
        self.assertEqual("created", status)
        return company

    def seed_credits(self, user_id, credits):
        self.repository.get_or_create_user(user_id)
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_users SET credits=? WHERE discord_user_id=?",
                (credits, user_id),
            )

    def seed_inventory(self, user_id, resource, quantity):
        self.repository.get_or_create_user(user_id)
        with immediate_transaction(self.database_path) as connection:
            actor_id = connection.execute(
                "SELECT id FROM industrial_actors WHERE discord_user_id=?", (user_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO industrial_inventory(actor_id,owner_discord_user_id,resource_type,quantity) "
                "VALUES(?,?,?,?) ON CONFLICT(actor_id,resource_type) "
                "DO UPDATE SET quantity=excluded.quantity",
                (actor_id, user_id, resource, quantity),
            )

    def test_schema_pragmas_and_version_are_persistent(self):
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])
            self.assertEqual([1, 2, 3, 4, 5], [r[0] for r in connection.execute(
                "SELECT version FROM industrial_schema_version"
            )])
            tables = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertIn("industrial_transactions", tables)
        self.assertIn("industrial_delivery_missions", tables)
        initialize_database_sync(self.database_path)
        self.assertEqual(0, self.repository.get_or_create_user(1).credits)

    def test_wallet_company_actor_and_restart(self):
        self.assertEqual(0, self.repository.get_or_create_user(100).credits)
        company = self.company(100, "miner")
        actor = self.repository.get_or_create_player_actor(100)
        self.assertEqual(100, actor.discord_user_id)
        self.assertIsNone(actor.ai_company_id)
        reopened = SQLiteIndustrialEconomyRepository(self.database_path)
        self.assertEqual(company.id, reopened.get_primary_company(100).id)
        self.assertEqual("miner", reopened.get_or_create_user(100).primary_job)

    def test_double_mine_collection_is_serialized(self):
        self.company(101, "miner")
        self.repository.get_or_create_and_refresh_mine(101)
        with immediate_transaction(self.database_path) as connection:
            connection.execute("UPDATE industrial_mines SET stock=37 WHERE owner_discord_user_id=101")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.repository.collect_mine(101), range(2)))
        self.assertEqual(37, sum(result[2].collected_quantity for result in results))
        self.assertEqual(37, self.repository.get_inventory(101)[0].quantity)

    def test_market_matching_conserves_credits_and_ore(self):
        self.company(201, "miner")
        self.company(202, "merchant")
        self.seed_inventory(201, "iron_ore", 50)
        self.seed_credits(202, 1_000)
        status, sell, _ = self.repository.create_market_order(
            201, "sell", "iron_ore", 50, 8, "sell-1"
        )
        self.assertEqual("ok", status)
        status, buy, _ = self.repository.create_market_order(
            202, "buy", "iron_ore", 50, 10, "buy-1"
        )
        self.assertEqual("ok", status)
        self.assertEqual(50, buy.filled_quantity)
        with closing(connect_database(self.database_path)) as connection:
            seller = connection.execute("SELECT credits FROM industrial_users WHERE discord_user_id=201").fetchone()[0]
            buyer = connection.execute("SELECT credits FROM industrial_users WHERE discord_user_id=202").fetchone()[0]
            escrow = connection.execute("SELECT sum(escrow_credits+escrow_quantity) FROM industrial_market_orders").fetchone()[0]
        self.assertEqual(400, seller)
        self.assertEqual(600, buyer)
        self.assertEqual(0, escrow)
        self.assertEqual(50, self.repository.get_inventory(202)[0].quantity)

    def test_duplicate_market_request_does_not_double_escrow(self):
        self.company(203, "miner")
        self.seed_inventory(203, "iron_ore", 20)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: self.repository.create_market_order(
                    203, "sell", "iron_ore", 10, 8, "same-sell"
                ),
                range(2),
            ))
        self.assertEqual({"ok", "duplicate"}, {result[0] for result in results})
        self.assertEqual(10, self.repository.get_inventory(203)[0].quantity)
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(1, connection.execute(
                "SELECT count(*) FROM industrial_market_orders"
            ).fetchone()[0])

    def test_upgrade_concurrency_never_makes_balance_negative(self):
        self.company(301, "miner")
        self.repository.get_or_create_and_refresh_mine(301)
        self.seed_credits(301, 250)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda value: self.repository.upgrade_mine(
                    301, "storage", f"upgrade-{value}"
                ),
                range(2),
            ))
        self.assertEqual(1, sum(result[0] == "ok" for result in results))
        with closing(connect_database(self.database_path)) as connection:
            credits = connection.execute("SELECT credits FROM industrial_users WHERE discord_user_id=301").fetchone()[0]
            level = connection.execute("SELECT storage_level FROM industrial_mines WHERE owner_discord_user_id=301").fetchone()[0]
        self.assertEqual(0, credits)
        self.assertEqual(2, level)

    def test_database_checks_reject_negative_assets(self):
        self.repository.get_or_create_user(401)
        with self.assertRaises(Exception):
            with immediate_transaction(self.database_path) as connection:
                connection.execute("UPDATE industrial_users SET credits=-1 WHERE discord_user_id=401")

    def test_end_to_end_transport_forge_shipment_and_world_sale(self):
        merchant, blacksmith, banker = 501, 502, 503
        self.company(merchant, "merchant")
        self.company(blacksmith, "blacksmith")
        self.company(banker, "banker")
        self.seed_credits(merchant, 10_000)
        self.seed_inventory(merchant, "iron_ore", 10)
        self.assertEqual("ok", self.repository.get_or_create_merchant(merchant)[0])
        status, _, _, transport = self.repository.start_transport(
            merchant, blacksmith, "iron_ore", 10, "ore-trip"
        )
        self.assertEqual("ok", status)
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_transports SET arrival_at=unixepoch()-1 WHERE id=?",
                (transport.transport.id,),
            )
        self.assertEqual("ok", self.repository.get_or_create_blacksmith(blacksmith)[0])
        self.assertEqual(10, self.repository.get_inventory(blacksmith)[0].quantity)

        status, _, _, process = self.repository.start_forge_job(
            blacksmith, "iron_ore", 10, "forge-job"
        )
        self.assertEqual("ok", status)
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_forge_jobs SET finishes_at=unixepoch()-1 WHERE id=?",
                (process.job.id,),
            )
        status, _, collection = self.repository.collect_forge_jobs(
            blacksmith, "forge-collect"
        )
        self.assertEqual("ok", status)
        self.assertEqual(10, collection.collected_quantity)

        status, _, _, shipment = self.repository.create_ingot_shipment(
            blacksmith, merchant, banker, 10, "shipment-create"
        )
        self.assertEqual("ok", status)
        status, _, _, accepted = self.repository.accept_ingot_shipment(
            merchant, shipment.shipment.id, "shipment-accept"
        )
        self.assertEqual("ok", status)
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_transports SET arrival_at=unixepoch()-1 WHERE id=?",
                (accepted.transport.id,),
            )
        self.assertEqual("ok", self.repository.get_or_create_banker(banker)[0])
        self.assertEqual(10, self.repository.get_inventory(banker)[0].quantity)
        status, _, remaining, sale = self.repository.sell_world_ingots(
            banker, 10, "world-sale"
        )
        self.assertEqual("ok", status)
        self.assertEqual(0, remaining)
        self.assertGreater(sale.total_credits, 0)
        self.assertEqual(0, self.repository.get_inventory(banker)[0].quantity)
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(1, connection.execute(
                "SELECT count(*) FROM industrial_transactions "
                "WHERE transaction_type='world_sale' AND monetary_effect='source'"
            ).fetchone()[0])

    def test_double_forge_collect_is_serialized(self):
        self.company(601, "blacksmith")
        self.seed_inventory(601, "iron_ore", 12)
        self.repository.get_or_create_blacksmith(601)
        process = self.repository.start_forge_job(601, "iron_ore", 12, "job-601")[3]
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_forge_jobs SET status='completed' WHERE id=?",
                (process.job.id,),
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda value: self.repository.collect_forge_jobs(601, f"collect-{value}"),
                range(2),
            ))
        self.assertEqual(12, sum(row[2].collected_quantity for row in results))
        inventory = {entry.resource_type: entry.quantity for entry in self.repository.get_inventory(601)}
        self.assertEqual(12, inventory["iron_ingot"])

    def test_double_contract_accept_transfers_once(self):
        creator, supplier = 701, 702
        self.seed_credits(creator, 500)
        self.seed_inventory(supplier, "iron_ore", 25)
        contract = self.repository.create_contract(
            creator, "iron_ore", 25, 500, "contract-create"
        )[2]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda value: self.repository.accept_contract(
                    supplier, contract.id, "contract-accept"
                ),
                range(2),
            ))
        self.assertEqual({"ok", "duplicate"}, {row[0] for row in results})
        self.assertEqual(500, self.repository.get_or_create_user(supplier).credits)
        creator_inventory = self.repository.get_inventory(creator)
        self.assertEqual(25, creator_inventory[0].quantity)
        supplier_inventory = self.repository.get_inventory(supplier)
        self.assertEqual(0, supplier_inventory[0].quantity)

    def test_double_delivery_accept_pays_only_once(self):
        merchant, blacksmith, courier_a, courier_b = 801, 802, 803, 804
        self.company(merchant, "merchant")
        self.company(blacksmith, "blacksmith")
        self.seed_credits(merchant, 10_000)
        self.seed_inventory(merchant, "iron_ore", 10)
        self.repository.get_or_create_merchant(merchant)
        transport = self.repository.start_transport(
            merchant, blacksmith, "iron_ore", 10, "delivery-trip"
        )[3].transport
        mission = self.repository.get_delivery_missions()[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda pair: self.repository.accept_delivery(
                    pair[0], mission.id, pair[1]
                ),
                ((courier_a, "delivery-a"), (courier_b, "delivery-b")),
            ))
        self.assertEqual(1, sum(row["result_status"] == "ok" for row in results))
        paid = sum(self.repository.get_or_create_user(user).credits for user in (courier_a, courier_b))
        with closing(connect_database(self.database_path)) as connection:
            escrow = connection.execute(
                "SELECT commission_max,commission_paid,merchant_refund,escrow_remaining "
                "FROM industrial_delivery_missions WHERE transport_id=?", (transport.id,)
            ).fetchone()
        self.assertEqual(0, escrow[3])
        self.assertEqual(escrow[0], escrow[1] + escrow[2])
        self.assertEqual(escrow[1], paid)


class SQLiteCrashResistanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "crash.db"
        self.repository = SQLiteIndustrialEconomyRepository(self.database_path)
        self.repository.get_or_create_user(1001)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def assert_integrity(self, path=None):
        with closing(connect_database(path or self.database_path)) as connection:
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())

    def test_production_path_uses_explicit_persistent_volume(self):
        with patch.dict(os.environ, {"INDUSTRIAL_DB_PATH": "/data/industrial_economy.db"}):
            self.assertEqual(Path("/data/industrial_economy.db"), get_database_path())

    def test_every_connection_has_required_pragmas(self):
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])
            self.assertEqual(5000, connection.execute("PRAGMA busy_timeout").fetchone()[0])
            self.assertEqual(1, connection.execute("PRAGMA synchronous").fetchone()[0])

    def test_committed_wallet_and_inventory_survive_connection_close(self):
        with immediate_transaction(self.database_path) as connection:
            actor_id = connection.execute(
                "SELECT id FROM industrial_actors WHERE discord_user_id=1001"
            ).fetchone()[0]
            connection.execute(
                "UPDATE industrial_users SET credits=725 WHERE discord_user_id=1001"
            )
            connection.execute(
                "INSERT INTO industrial_inventory(actor_id,owner_discord_user_id,resource_type,quantity) "
                "VALUES(?,1001,'iron_ore',42)", (actor_id,)
            )
        reopened = SQLiteIndustrialEconomyRepository(self.database_path)
        self.assertEqual(725, reopened.get_or_create_user(1001).credits)
        self.assertEqual(42, reopened.get_inventory(1001)[0].quantity)
        self.assert_integrity()

    def test_abandoned_uncommitted_transaction_is_rolled_back_on_close(self):
        connection = connect_database(self.database_path)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE industrial_users SET credits=999 WHERE discord_user_id=1001"
        )
        connection.close()
        reopened = SQLiteIndustrialEconomyRepository(self.database_path)
        self.assertEqual(0, reopened.get_or_create_user(1001).credits)
        self.assert_integrity()

    def test_exception_rolls_back_wallet_and_mine_upgrade(self):
        self.repository.create_first_company(1001, "Crash Mine", "miner")
        self.repository.get_or_create_and_refresh_mine(1001)
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_users SET credits=250 WHERE discord_user_id=1001"
            )
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            with immediate_transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE industrial_users SET credits=credits-250 WHERE discord_user_id=1001"
                )
                raise RuntimeError("simulated crash")
                connection.execute(
                    "UPDATE industrial_mines SET storage_level=storage_level+1 "
                    "WHERE owner_discord_user_id=1001"
                )
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(250, connection.execute(
                "SELECT credits FROM industrial_users WHERE discord_user_id=1001"
            ).fetchone()[0])
            self.assertEqual(1, connection.execute(
                "SELECT storage_level FROM industrial_mines WHERE owner_discord_user_id=1001"
            ).fetchone()[0])
        self.assert_integrity()

    def test_exception_rolls_back_market_escrow_and_mine_collection(self):
        self.repository.create_first_company(1001, "Crash Mine", "miner")
        self.repository.get_or_create_and_refresh_mine(1001)
        with immediate_transaction(self.database_path) as connection:
            actor_id = connection.execute(
                "SELECT id FROM industrial_actors WHERE discord_user_id=1001"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO industrial_inventory(actor_id,owner_discord_user_id,resource_type,quantity) "
                "VALUES(?,1001,'iron_ore',50)", (actor_id,)
            )
            connection.execute(
                "UPDATE industrial_mines SET stock=20 WHERE owner_discord_user_id=1001"
            )
        with self.assertRaises(RuntimeError):
            with immediate_transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE industrial_inventory SET quantity=quantity-30 "
                    "WHERE owner_discord_user_id=1001 AND resource_type='iron_ore'"
                )
                raise RuntimeError("market escrow crash")
        with self.assertRaises(RuntimeError):
            with immediate_transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE industrial_inventory SET quantity=quantity+20 "
                    "WHERE owner_discord_user_id=1001 AND resource_type='iron_ore'"
                )
                raise RuntimeError("mine collection crash")
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(50, connection.execute(
                "SELECT quantity FROM industrial_inventory "
                "WHERE owner_discord_user_id=1001 AND resource_type='iron_ore'"
            ).fetchone()[0])
            self.assertEqual(20, connection.execute(
                "SELECT stock FROM industrial_mines WHERE owner_discord_user_id=1001"
            ).fetchone()[0])
            self.assertEqual(0, connection.execute(
                "SELECT count(*) FROM industrial_market_orders"
            ).fetchone()[0])
        self.assert_integrity()

    def test_exception_rolls_back_contract_escrow(self):
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_users SET credits=400 WHERE discord_user_id=1001"
            )
        with self.assertRaises(RuntimeError):
            with immediate_transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE industrial_users SET credits=credits-400 WHERE discord_user_id=1001"
                )
                raise RuntimeError("contract creation crash")
        with closing(connect_database(self.database_path)) as connection:
            self.assertEqual(400, connection.execute(
                "SELECT credits FROM industrial_users WHERE discord_user_id=1001"
            ).fetchone()[0])
            self.assertEqual(0, connection.execute(
                "SELECT count(*) FROM industrial_contracts"
            ).fetchone()[0])
        self.assert_integrity()

    def test_wal_allows_read_during_write_and_waits_for_writer(self):
        writer = connect_database(self.database_path)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "UPDATE industrial_users SET credits=10 WHERE discord_user_id=1001"
        )
        with closing(connect_database(self.database_path)) as reader:
            self.assertEqual(0, reader.execute(
                "SELECT credits FROM industrial_users WHERE discord_user_id=1001"
            ).fetchone()[0])

        result = {}
        started = threading.Event()

        def concurrent_writer():
            connection = connect_database(self.database_path)
            started.set()
            before = time.monotonic()
            connection.execute("BEGIN IMMEDIATE")
            result["waited"] = time.monotonic() - before
            connection.execute(
                "UPDATE industrial_users SET credits=credits+5 WHERE discord_user_id=1001"
            )
            connection.commit()
            connection.close()

        thread = threading.Thread(target=concurrent_writer)
        thread.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.15)
        writer.commit()
        writer.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(result["waited"], 0.10)
        self.assertEqual(15, self.repository.get_or_create_user(1001).credits)
        self.assert_integrity()

    def test_sqlite_backup_is_consistent_and_integral(self):
        with immediate_transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE industrial_users SET credits=321 WHERE discord_user_id=1001"
            )
        destination = Path(self.temporary_directory.name) / "backup.db"
        backup_database(destination, self.database_path)
        with closing(connect_database(destination)) as connection:
            self.assertEqual(321, connection.execute(
                "SELECT credits FROM industrial_users WHERE discord_user_id=1001"
            ).fetchone()[0])
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())


class SQLiteAsyncConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_collect_uses_one_sqlite_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "concurrency.db"
            repository = SQLiteIndustrialEconomyRepository(path)
            repository.create_first_company(901, "Async Mine", "miner")
            repository.get_or_create_and_refresh_mine(901)
            with immediate_transaction(path) as connection:
                connection.execute(
                    "UPDATE industrial_mines SET stock=64 WHERE owner_discord_user_id=901"
                )
            first, second = await asyncio.gather(
                asyncio.to_thread(repository.collect_mine, 901),
                asyncio.to_thread(repository.collect_mine, 901),
            )
            self.assertEqual(64, first[2].collected_quantity + second[2].collected_quantity)
            self.assertEqual(64, repository.get_inventory(901)[0].quantity)


if __name__ == "__main__":
    unittest.main()
