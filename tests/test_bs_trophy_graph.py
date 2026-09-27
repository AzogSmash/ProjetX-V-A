import ast
import io
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Le test de la couche Supabase ne doit pas nécessiter une connexion ni le SDK
# installé dans l'environnement de test : son client est intégralement simulé.
_supabase = types.ModuleType("supabase")
_supabase.create_client = lambda *args, **kwargs: None
_supabase.Client = object
sys.modules["supabase"] = _supabase
import db_bs
from bs_trophy_graph import parse_trophygraph_args, render_trophy_graph, trophy_summary

class TrophyGraphTests(unittest.TestCase):
    points = [
        {"snapshot_date": "2026-09-20", "trophies": 50_000},
        {"snapshot_date": "2026-09-21", "trophies": 50_240},
        {"snapshot_date": "2026-09-22", "trophies": 50_100},
        {"snapshot_date": "2026-09-23", "trophies": 50_600},
    ]

    def test_default_member_and_all_valid_periods(self):
        self.assertEqual((None, 30, None), parse_trophygraph_args(None, None))
        self.assertEqual(("42", 30, None), parse_trophygraph_args("<@42>", "30"))
        for period in ("7", "30", "90"):
            self.assertEqual((None, int(period), None), parse_trophygraph_args(period, None))

    def test_invalid_period_and_target_are_rejected(self):
        self.assertIn("Période invalide", parse_trophygraph_args("8", None)[2])
        self.assertIn("Période invalide", parse_trophygraph_args("<@42>", "100")[2])
        self.assertIn("Indique un membre", parse_trophygraph_args("not-a-member", None)[2])

    def test_real_variations_and_positive_negative_progress(self):
        summary = trophy_summary(self.points)
        self.assertEqual(600, summary["delta"])
        self.assertEqual(50_000, summary["minimum"])
        self.assertEqual(50_600, summary["maximum"])
        down = trophy_summary([self.points[0], {"snapshot_date": "2026-09-21", "trophies": 49_900}])
        self.assertEqual(-100, down["delta"])

    def test_image_is_a_real_png_for_partial_history(self):
        image = render_trophy_graph("Tawheed", self.points, 90)
        self.assertIsInstance(image, io.BytesIO)
        self.assertTrue(image.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image.getvalue()), 5_000)

    def test_snapshot_uses_existing_daily_unique_key(self):
        calls = []

        class Table:
            def __init__(self, name): self.name = name
            def upsert(self, value, **kwargs): calls.append((self.name, value, kwargs)); return self
            def execute(self): return self

        class Client:
            def table(self, name): return Table(name)

        with patch.object(db_bs, "get_client", return_value=Client()):
            db_bs.upsert_player_snapshot("2026-09-27", "#ABC", "Player", 123)
            db_bs.upsert_player_snapshot("2026-09-27", "#ABC", "Player", 123)
        snapshots = [call for call in calls if call[0] == "bs_trophy_snapshots"]
        self.assertEqual(2, len(snapshots))
        self.assertEqual("player_tag,snapshot_date", snapshots[0][2]["on_conflict"])
        self.assertEqual("ABC", snapshots[0][1]["player_tag"])

    def test_history_is_sorted_even_if_the_database_response_is_not(self):
        class Query:
            def select(self, *_): return self
            def eq(self, *_): return self
            def order(self, *_): return self
            def gte(self, *_): return self
            def lte(self, *_): return self
            def execute(self):
                return types.SimpleNamespace(data=[
                    {"snapshot_date": "2026-09-22", "trophies": 120},
                    {"snapshot_date": "2026-09-20", "trophies": 100},
                    {"snapshot_date": "2026-09-21", "trophies": 110},
                    {"snapshot_date": "2099-01-01", "trophies": 999_999},
                ])

        class Client:
            def table(self, _): return Query()

        with patch.object(db_bs, "get_client", return_value=Client()):
            history = db_bs.get_player_history("ABC", since="2026-09-20")
        self.assertEqual(["2026-09-20", "2026-09-21", "2026-09-22"], [row["snapshot_date"] for row in history])

    def test_snapshot_write_failure_is_non_blocking_for_brawl_refresh(self):
        tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_record_linked_bs_snapshot")
        calls = []
        namespace = {
            "db_bs": types.SimpleNamespace(upsert_player_snapshot=lambda *args: (_ for _ in ()).throw(RuntimeError("down"))),
            "datetime": datetime,
            "BS_SEASON_TZ": timezone.utc,
            "logging": types.SimpleNamespace(warning=lambda *args, **kwargs: calls.append(args)),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
        self.assertIsNone(namespace["_record_linked_bs_snapshot"]({"tag": "#ABC", "name": "P", "trophies": 1}))
        self.assertEqual(1, len(calls))

    def test_crypto_graph_and_new_command_names_do_not_collide(self):
        tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
        commands = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr not in {"command", "hybrid_command"}:
                    continue
                name = node.name.removeprefix("cmd_")
                aliases = []
                for keyword in decorator.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant): name = keyword.value.value
                    if keyword.arg == "aliases" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                        aliases = [item.value for item in keyword.value.elts if isinstance(item, ast.Constant)]
                commands[name] = (node.name, aliases)
        self.assertEqual(("cmd_graphique", ["chart", "courbe", "graph"]), commands["graphique"])
        self.assertEqual(("cmd_trophygraph", ["tg", "tropheesgraph"]), commands["trophygraph"])
        all_names = [name for name, (_, aliases) in commands.items() for name in [name, *aliases]]
        self.assertEqual(1, all_names.count("graph"))
        self.assertNotIn("graph", commands["trophygraph"][1])

    def test_command_uses_persisted_history_without_live_api_call(self):
        source = Path("main.py").read_text(encoding="utf-8")
        command = source[source.index("async def cmd_trophygraph"):source.index("@bot.command(name=\"bs_roles\"")]
        self.assertIn("db_bs.get_player_history", command)
        self.assertNotIn("_bs_fetch_player", command)
        self.assertIn("Pas encore assez de données", command)
        self.assertIn("pas encore lié de compte", command)

    def test_existing_snapshots_schema_is_persistent_and_deduplicated(self):
        schema = Path("supabase/001_bs_tracking.sql").read_text(encoding="utf-8")
        self.assertIn("create table if not exists bs_trophy_snapshots", schema)
        self.assertIn("primary key (player_tag, snapshot_date)", schema)


if __name__ == "__main__":
    unittest.main()
