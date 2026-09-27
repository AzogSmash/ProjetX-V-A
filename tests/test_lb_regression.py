import ast
import unittest
from pathlib import Path
from unittest.mock import patch


class LeaderboardCommandRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("main.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_lb_alias_remains_registered_on_coin_leaderboard(self):
        command = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "cmd_classement"
        )
        decorator = next(
            node for node in command.decorator_list
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "hybrid_command"
        )
        kwargs = {keyword.arg: keyword.value for keyword in decorator.keywords}
        self.assertEqual("classement", kwargs["name"].value)
        self.assertEqual(["top", "leaderboard", "lb"], [item.value for item in kwargs["aliases"].elts])

    def test_leaderboard_handler_keeps_server_data_and_response_path(self):
        command = next(node for node in ast.walk(self.tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "cmd_classement")
        text = ast.get_source_segment(self.source, command)
        self.assertIn("ctx.guild.members", text)
        self.assertIn("ctx.send(embed=embed)", text)
        self.assertIn("crypto_holdings", text)
        self.assertIn("cold_wallets", text)

    def test_timezone_dependency_is_declared_for_startup_import(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("tzdata", [line.strip().lower() for line in requirements])


class LeaderboardRuntimeRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_numbers_and_mixed_cold_batches_are_safe(self):
        import main

        self.assertEqual(0, main._lb_number(None))
        self.assertEqual(0, main._lb_number(""))
        self.assertEqual(0, main._lb_number("not-a-number"))
        self.assertEqual(0, main._lb_number(float("nan")))
        self.assertEqual(0, main._lb_number(float("inf")))
        self.assertEqual(0, main._lb_number(float("-inf")))
        self.assertEqual(-5, main._lb_number("-5"))

        with patch.object(main, "crypto_prices", {"BTC": 100}):
            wallet = {
                "BTC": [{"qty": "2"}, {"qty": "bad"}, {"qty": "3"}],
                "UNKNOWN": [{"qty": "9"}],
                "ETH": "broken",
            }
            self.assertEqual(500, main._lb_cold_value(wallet))

    async def test_realistic_members_and_all_economic_sources_send_embed(self):
        import main

        class Member:
            def __init__(self, uid, name, bot=False):
                self.id, self.display_name, self.bot = uid, name, bot

        class Guild:
            id = 123
            members = [Member(1, "A"), Member(2, "B"), Member(3, "C"), Member(4, "Bot", True)]

            def get_member(self, uid):
                return next((member for member in self.members if member.id == uid), None)

        class Context:
            guild = Guild()

            def __init__(self):
                self.sent = []

            async def send(self, *args, **kwargs):
                self.sent.append((args, kwargs))

        ctx = Context()
        with patch.object(main, "coins", {1: "1000", 2: 1500, 3: 0}), \
             patch.object(main, "safes", {"1": "500"}), \
             patch.object(main, "crypto_prices", {"BTC": 100, "ETH": 100}), \
             patch.object(main, "crypto_holdings", {"1": {"BTC": "2"}, "2": {"UNKNOWN": 5}}), \
             patch.object(main, "cold_wallets", {"1": {"ETH": [{"qty": "3"}]}, "2": {"BTC": {"qty": "bad"}}}):
            command = main.bot.get_command("lb")
            await command.callback(ctx)

        self.assertEqual(1, len(ctx.sent))
        embed = ctx.sent[0][1]["embed"]
        self.assertIn("A", embed.description)
        self.assertIn("2,000", embed.description)
        self.assertIn("1,500", embed.description)
        self.assertIn("C", embed.description)


if __name__ == "__main__":
    unittest.main()
