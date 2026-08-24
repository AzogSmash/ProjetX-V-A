import asyncio
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.commands.company import validate_company_name
from economy_v2.jobs import resolve_job
from economy_v2.models import IndustrialCompany, IndustrialUser
from economy_v2.services import (
    CompanyAlreadyExistsError,
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


class InMemoryRepository:
    def __init__(self) -> None:
        self.users = {}
        self.companies = {}
        self._lock = threading.Lock()

    def get_or_create_user(self, user_id: int) -> IndustrialUser:
        with self._lock:
            return self.users.setdefault(user_id, IndustrialUser(user_id, 0, None))

    def get_primary_company(self, user_id: int):
        with self._lock:
            return self.companies.get(user_id)

    def create_first_company(self, user_id: int, name: str, job_type: str):
        with self._lock:
            if user_id in self.companies:
                return "already_exists", None
            company = IndustrialCompany(1, user_id, name, job_type, 1, True)
            self.companies[user_id] = company
            self.users[user_id] = IndustrialUser(user_id, 0, job_type)
            return "created", company


class JobAndNameTests(unittest.TestCase):
    def test_french_job_mapping(self) -> None:
        self.assertEqual(resolve_job("mineur").key, "miner")
        self.assertEqual(resolve_job("marchand").key, "merchant")
        self.assertEqual(resolve_job("forgeron").key, "blacksmith")
        self.assertEqual(resolve_job("banquier").key, "banker")

    def test_english_job_mapping(self) -> None:
        for job in ("miner", "merchant", "blacksmith", "banker"):
            self.assertEqual(resolve_job(job).key, job)

    def test_invalid_job(self) -> None:
        self.assertIsNone(resolve_job("pirate"))

    def test_company_name_validation(self) -> None:
        self.assertEqual(validate_company_name("  Les Mines du Nord  ")[0], "Les Mines du Nord")
        self.assertIsNotNone(validate_company_name("  ")[1])
        self.assertIsNotNone(validate_company_name("ab")[1])
        self.assertIsNotNone(validate_company_name("x" * 41)[1])
        self.assertIsNotNone(validate_company_name("Société <@123>")[1])
        self.assertIsNotNone(validate_company_name("Société\u0000")[1])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryRepository()
        self.service = SupabaseIndustrialEconomyService(self.repository)

    async def test_wallet_auto_creates_zero_credit_profile(self) -> None:
        self.assertEqual(await self.service.get_balance(123), 0)
        self.assertIn(123, self.repository.users)

    async def test_first_company_sets_matching_primary_job(self) -> None:
        company = await self.service.create_first_company(123, "Azog Industries", "miner")
        self.assertEqual(company.job_type, "miner")
        self.assertEqual(self.repository.users[123].primary_job, company.job_type)

    async def test_second_company_is_refused(self) -> None:
        await self.service.create_first_company(123, "Azog Industries", "miner")
        with self.assertRaises(CompanyAlreadyExistsError):
            await self.service.create_first_company(123, "Azog Bank", "banker")

    async def test_concurrent_first_company_creation_has_one_winner(self) -> None:
        results = await asyncio.gather(
            self.service.create_first_company(123, "Entreprise A", "miner"),
            self.service.create_first_company(123, "Entreprise B", "banker"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, IndustrialCompany) for result in results), 1)
        self.assertEqual(sum(isinstance(result, CompanyAlreadyExistsError) for result in results), 1)
        self.assertEqual(len(self.repository.companies), 1)


class CompanyCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryRepository()
        self.service = SupabaseIndustrialEconomyService(self.repository)
        self.router = build_economy_router(self.service)

    async def test_company_without_company(self) -> None:
        message = FakeMessage("?company")
        await self.router.handle(message)
        self.assertIn("aucune entreprise", message.channel.sent[0][0])

    async def test_company_create_parsing_uses_full_name(self) -> None:
        message = FakeMessage("?company create mineur Les Mines du Nord")
        await self.router.handle(message)
        self.assertEqual(self.repository.companies[123].name, "Les Mines du Nord")
        self.assertEqual(self.repository.companies[123].job_type, "miner")

    async def test_company_display(self) -> None:
        await self.service.create_first_company(123, "Azog Industries", "miner")
        message = FakeMessage("?company")
        await self.router.handle(message)
        embed = message.channel.sent[0][1]["embed"]
        self.assertEqual(embed.title, "🏢 Azog Industries")

    async def test_second_company_command_is_refused(self) -> None:
        await self.service.create_first_company(123, "Azog Industries", "miner")
        message = FakeMessage("?company create banquier Azog Bank")
        await self.router.handle(message)
        self.assertIn("déjà une entreprise", message.channel.sent[0][0])


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = (PROJECT_ROOT / "supabase" / "024_industrial_economy.sql").read_text(encoding="utf-8").casefold()

    def test_required_database_guards(self) -> None:
        for fragment in (
            "discord_user_id bigint primary key",
            "credits bigint not null default 0 check (credits >= 0)",
            "level integer not null default 1 check (level >= 1)",
            "created_at timestamptz not null default now()",
            "updated_at timestamptz not null default now()",
            "create unique index if not exists industrial_companies_one_first_per_owner_idx",
            "pg_advisory_xact_lock",
            "set search_path = ''",
            "from public, anon, authenticated",
            "enforce_first_company_job_consistency",
            "enforce_industrial_primary_job_lock",
            "alter table industrial_users enable row level security",
            "alter table industrial_companies enable row level security",
        ):
            self.assertIn(fragment, self.sql)

    def test_economy_package_does_not_reference_legacy_storage(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PROJECT_ROOT / "economy_v2").rglob("*.py")
        ).casefold()
        self.assertNotIn("data.json", source)
        self.assertNotIn("from main import coins", source)
        self.assertNotIn("coins[", source)


if __name__ == "__main__":
    unittest.main()
