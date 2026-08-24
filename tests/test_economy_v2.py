import unittest
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.models import IndustrialCompany, IndustrialUser
from economy_v2.router import (
    find_command_name_collisions,
    parse_economy_message,
    validate_command_names,
)


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.author = SimpleNamespace(id=123, mention="<@123>")
        self.channel = FakeChannel()


class FakeEconomyService:
    def __init__(self) -> None:
        self.users = {}
        self.companies = {}

    async def get_or_create_user(self, user_id: int) -> IndustrialUser:
        return self.users.setdefault(user_id, IndustrialUser(user_id, 0, None))

    async def get_balance(self, user_id: int) -> int:
        return (await self.get_or_create_user(user_id)).credits

    async def get_primary_company(self, user_id: int):
        await self.get_or_create_user(user_id)
        return self.companies.get(user_id)

    async def create_first_company(self, user_id: int, name: str, job_type: str):
        company = IndustrialCompany(1, user_id, name, job_type, 1, True)
        self.companies[user_id] = company
        self.users[user_id] = IndustrialUser(user_id, 0, job_type)
        return company


class UnavailableActivityService(FakeEconomyService):
    def __init__(self) -> None:
        super().__init__()
        self.activity_calls = 0

    async def record_activity(self, user_id: int) -> None:
        self.activity_calls += 1
        raise RuntimeError("Supabase unavailable")


class UnavailableDatabaseService(UnavailableActivityService):
    async def get_or_create_user(self, user_id: int) -> IndustrialUser:
        raise RuntimeError("Supabase unavailable")

    async def get_balance(self, user_id: int) -> int:
        raise RuntimeError("Supabase unavailable")


class FakeCommand:
    def __init__(self, name: str, aliases=()) -> None:
        self.name = name
        self.aliases = list(aliases)


class FakeBot:
    def __init__(self, commands) -> None:
        self._commands = commands

    def walk_commands(self):
        return iter(self._commands)


class ParsingTests(unittest.TestCase):
    def test_parses_command_and_arguments(self) -> None:
        parsed = parse_economy_message("?market iron 100")
        self.assertEqual(parsed.command, "market")
        self.assertEqual(parsed.args, ("iron", "100"))

    def test_ignores_extra_spaces_and_command_case(self) -> None:
        parsed = parse_economy_message("  ?   ECOHELP   ")
        self.assertEqual(parsed.command, "ecohelp")
        self.assertEqual(parsed.args, ())

    def test_question_mark_in_sentence_is_ignored(self) -> None:
        self.assertIsNone(parse_economy_message("bonjour ?"))

    def test_bare_prefix_is_safe(self) -> None:
        parsed = parse_economy_message("?")
        self.assertEqual(parsed.command, "")
        self.assertEqual(parsed.args, ())


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_is_case_insensitive(self) -> None:
        message = FakeMessage("?ECOHELP")
        handled = await build_economy_router(FakeEconomyService()).handle(message)
        self.assertTrue(handled)
        self.assertIn("embed", message.channel.sent[0][1])

    async def test_help_is_static_when_supabase_is_unavailable(self) -> None:
        service = UnavailableDatabaseService()
        message = FakeMessage("?ecohelp")
        await build_economy_router(service).handle(message)
        self.assertEqual(service.activity_calls, 0)
        self.assertIn("embed", message.channel.sent[0][1])
        embed = message.channel.sent[0][1]["embed"]
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))

    async def test_activity_failure_does_not_hide_database_command_result(self) -> None:
        service = UnavailableActivityService()
        message = FakeMessage("?wallet")
        await build_economy_router(service).handle(message)
        self.assertEqual(service.activity_calls, 1)
        self.assertIn("0 CR", message.channel.sent[0][1]["embed"].description)

    async def test_wallet_uses_industrial_placeholder(self) -> None:
        message = FakeMessage("?wallet")
        await build_economy_router(FakeEconomyService()).handle(message)
        self.assertIn("0 CR", message.channel.sent[0][1]["embed"].description)

    async def test_unknown_command_has_clean_response(self) -> None:
        message = FakeMessage("?azerty")
        await build_economy_router(FakeEconomyService()).handle(message)
        self.assertIn("Commande économique inconnue", message.channel.sent[0][0])

    async def test_ordinary_message_is_not_handled(self) -> None:
        message = FakeMessage("bonjour ?")
        self.assertFalse(await build_economy_router(FakeEconomyService()).handle(message))
        self.assertEqual(message.channel.sent, [])


class CollisionTests(unittest.TestCase):
    def test_detects_primary_name(self) -> None:
        bot = FakeBot([FakeCommand("wallet")])
        self.assertEqual(find_command_name_collisions(bot, {"wallet"}), {"wallet"})

    def test_detects_alias_case_insensitively(self) -> None:
        bot = FakeBot([FakeCommand("coins", aliases=["WALLET"])])
        with self.assertRaisesRegex(RuntimeError, '"wallet"'):
            validate_command_names(bot, {"wallet"})

    def test_distinct_names_do_not_collide(self) -> None:
        bot = FakeBot([FakeCommand("balance", aliases=["bal"])])
        validate_command_names(bot, {"wallet", "ecohelp"})


if __name__ == "__main__":
    unittest.main()
