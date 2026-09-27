import ast
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
