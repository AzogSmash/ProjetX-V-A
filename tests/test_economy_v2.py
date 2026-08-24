import unittest
from types import SimpleNamespace

from economy_v2 import build_economy_router
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
        self.author = SimpleNamespace(id=123)
        self.channel = FakeChannel()


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
        handled = await build_economy_router().handle(message)
        self.assertTrue(handled)
        self.assertIn("embed", message.channel.sent[0][1])

    async def test_wallet_uses_industrial_placeholder(self) -> None:
        message = FakeMessage("?wallet")
        await build_economy_router().handle(message)
        self.assertIn("0 CR", message.channel.sent[0][0])

    async def test_unknown_command_has_clean_response(self) -> None:
        message = FakeMessage("?azerty")
        await build_economy_router().handle(message)
        self.assertIn("Commande économique inconnue", message.channel.sent[0][0])

    async def test_ordinary_message_is_not_handled(self) -> None:
        message = FakeMessage("bonjour ?")
        self.assertFalse(await build_economy_router().handle(message))
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
