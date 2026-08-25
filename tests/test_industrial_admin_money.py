import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.database import connect_database
from economy_v2.services import SQLiteIndustrialEconomyService
from economy_v2.sqlite_repository import SQLiteIndustrialEconomyRepository


class FakeMember:
    def __init__(self, user_id: int, *, administrator: bool = False) -> None:
        self.id = user_id
        self.mention = f"<@{user_id}>"
        self.guild_permissions = SimpleNamespace(administrator=administrator)


class FakeGuild:
    def __init__(self, *members: FakeMember) -> None:
        self.members = {member.id: member for member in members}

    def get_member(self, user_id: int):
        return self.members.get(user_id)


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, content: str, message_id: int, author: FakeMember,
                 guild: FakeGuild) -> None:
        self.content = content
        self.id = message_id
        self.author = author
        self.guild = guild
        self.channel = FakeChannel()


class AdminMoneyRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "industrial.db"
        self.repository = SQLiteIndustrialEconomyRepository(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_admin_can_add_and_profile_is_created(self) -> None:
        result = self.repository.adjust_admin_credits(1, 2, "add", 5_000, "add-1")
        self.assertEqual((0, 5_000), (result.balance_before, result.balance_after))
        self.assertEqual(5_000, self.repository.get_or_create_user(2).credits)

    def test_admin_can_remove_without_negative_balance(self) -> None:
        self.repository.adjust_admin_credits(1, 2, "add", 5_000, "seed")
        result = self.repository.adjust_admin_credits(1, 2, "remove", 2_500, "remove-1")
        self.assertEqual((5_000, 2_500), (result.balance_before, result.balance_after))
        self.assertEqual(2_500, self.repository.get_or_create_user(2).credits)

    def test_maximum_amount_is_accepted(self) -> None:
        result = self.repository.adjust_admin_credits(
            1, 2, "add", 1_000_000_000, "maximum",
        )
        self.assertEqual(1_000_000_000, result.balance_after)

    def test_insufficient_remove_changes_nothing_and_never_goes_negative(self) -> None:
        self.repository.adjust_admin_credits(1, 2, "add", 1_000, "seed")
        result = self.repository.adjust_admin_credits(1, 2, "remove", 2_500, "too-much")
        self.assertEqual("insufficient_funds", result.status)
        self.assertEqual(1_000, self.repository.get_or_create_user(2).credits)
        with closing(connect_database(self.database_path)) as connection:
            count = connection.execute(
                "SELECT count(*) FROM industrial_admin_credit_requests "
                "WHERE request_id='too-much'"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_same_request_is_idempotent(self) -> None:
        first = self.repository.adjust_admin_credits(1, 2, "add", 5_000, "same")
        second = self.repository.adjust_admin_credits(1, 2, "add", 5_000, "same")
        self.assertFalse(first.duplicate_request)
        self.assertTrue(second.duplicate_request)
        self.assertEqual(5_000, self.repository.get_or_create_user(2).credits)

    def test_reused_request_with_different_parameters_is_rejected(self) -> None:
        self.repository.adjust_admin_credits(1, 2, "add", 5_000, "same")
        with self.assertRaisesRegex(ValueError, "request id parameter mismatch"):
            self.repository.adjust_admin_credits(1, 2, "remove", 5_000, "same")

    def test_audit_records_admin_source_and_sink(self) -> None:
        self.repository.adjust_admin_credits(10, 20, "add", 5_000, "source")
        self.repository.adjust_admin_credits(10, 20, "remove", 2_000, "sink")
        with closing(connect_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT transaction_type,monetary_effect,credits,metadata "
                "FROM industrial_transactions WHERE transaction_type LIKE 'admin_credit_%' "
                "ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [("admin_credit_add", "source", 5_000),
             ("admin_credit_remove", "sink", 2_000)],
            [(row[0], row[1], row[2]) for row in rows],
        )
        metadata = json.loads(rows[0][3])
        self.assertEqual(10, metadata["admin_discord_user_id"])
        self.assertEqual(20, metadata["target_discord_user_id"])
        self.assertEqual((0, 5_000), (metadata["balance_before"], metadata["balance_after"]))
        self.assertEqual("source", metadata["request_id"])

    def test_economy_stats_separate_admin_source_and_sink(self) -> None:
        self.repository.adjust_admin_credits(1, 2, "add", 5_000, "source")
        self.repository.adjust_admin_credits(1, 2, "remove", 2_000, "sink")
        self.assertEqual(
            {"admin_credit_sources": 5_000, "admin_credit_sinks": 2_000},
            self.repository.get_admin_credit_stats(),
        )

    def test_two_concurrent_adjustments_keep_both_updates(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda request_id: self.repository.adjust_admin_credits(
                    1, 2, "add", 100, request_id,
                ),
                ("concurrent-1", "concurrent-2"),
            ))
        self.assertEqual([100, 200], sorted(result.balance_after for result in results))
        self.assertEqual(200, self.repository.get_or_create_user(2).credits)

    def test_repository_rejects_out_of_range_amounts(self) -> None:
        for amount in (0, -1, 1_000_000_001):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.repository.adjust_admin_credits(1, 2, "add", amount, str(amount))


class AdminMoneyCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        path = Path(self.temporary_directory.name) / "industrial.db"
        self.repository = SQLiteIndustrialEconomyRepository(path)
        self.service = SQLiteIndustrialEconomyService(self.repository)
        self.admin = FakeMember(100, administrator=True)
        self.user = FakeMember(200)
        self.guild = FakeGuild(self.admin, self.user)
        self.router = build_economy_router(self.service)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def send(self, content: str, message_id: int = 1,
                   author: FakeMember | None = None) -> FakeMessage:
        message = FakeMessage(content, message_id, author or self.admin, self.guild)
        await self.router.handle(message)
        return message

    async def test_admin_add_and_short_alias_remove(self) -> None:
        added = await self.send("?adminmoney add <@200> 5000", 1)
        removed = await self.send("?am remove 200 2500", 2)
        self.assertEqual("💳 Crédits ajoutés", added.channel.sent[0][1]["embed"].title)
        self.assertEqual("💳 Crédits retirés", removed.channel.sent[0][1]["embed"].title)
        self.assertEqual(2_500, self.repository.get_or_create_user(200).credits)

    async def test_non_admin_is_refused_without_database_change(self) -> None:
        message = await self.send("?adminmoney add <@200> 5000", author=self.user)
        self.assertEqual(
            "❌ Tu n'as pas la permission d'utiliser cette commande.",
            message.channel.sent[0][0],
        )
        with closing(connect_database(self.repository.database_path)) as connection:
            self.assertEqual(0, connection.execute(
                "SELECT count(*) FROM industrial_users"
            ).fetchone()[0])

    async def test_zero_negative_float_and_scientific_amounts_are_refused(self) -> None:
        for message_id, amount in enumerate(("0", "-1", "1.5", "1e3"), start=10):
            with self.subTest(amount=amount):
                message = await self.send(
                    f"?adminmoney add <@200> {amount}", message_id,
                )
                self.assertIn("doit être un entier", message.channel.sent[0][0])
        with closing(connect_database(self.repository.database_path)) as connection:
            self.assertEqual(0, connection.execute(
                "SELECT count(*) FROM industrial_admin_credit_requests"
            ).fetchone()[0])

    async def test_unknown_user_is_refused(self) -> None:
        message = await self.send("?adminmoney add 999 5000")
        self.assertEqual("❌ Utilisateur invalide.", message.channel.sent[0][0])

    async def test_command_names_include_both_aliases_and_help_stays_hidden(self) -> None:
        self.assertIn("adminmoney", self.router.command_names)
        self.assertIn("am", self.router.command_names)
        help_message = await self.send("?ecohelp")
        embed = help_message.channel.sent[0][1]["embed"]
        self.assertNotIn("adminmoney", " ".join(field.value for field in embed.fields))

    async def test_no_legacy_economy_storage_reference(self) -> None:
        root = Path(__file__).parents[1]
        source = "\n".join(
            (root / path).read_text(encoding="utf-8").casefold()
            for path in (
                "economy_v2/commands/admin_money.py",
                "economy_v2/sqlite_repository.py",
            )
        )
        for forbidden in ("data.json", "coins", "supabase"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
