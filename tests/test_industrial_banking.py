import pathlib
import unittest

from economy_v2.world_market_config import bounded_world_price


ROOT = pathlib.Path(__file__).parents[1]
SQL = (ROOT / "supabase" / "030_industrial_banking.sql").read_text(encoding="utf-8")


class WorldPriceTests(unittest.TestCase):
    def test_price_is_deterministic_and_bounded(self):
        self.assertEqual(bounded_world_price(0), 80)
        self.assertEqual(bounded_world_price(1_000), 79)
        self.assertEqual(bounded_world_price(100_000), 50)


class BankingMigrationTests(unittest.TestCase):
    def test_lazy_delivery_and_atomic_sale(self):
        for token in ("arrival_at <= current_time", "for update", "status = 'delivered'",
                      "quantity=quantity-p_quantity", "credits=credits+total",
                      "request_id text not null unique", "pg_advisory_xact_lock(9000000001)"):
            self.assertIn(token, SQL)

    def test_security_and_role_consistency(self):
        self.assertIn("enable row level security", SQL)
        self.assertGreaterEqual(SQL.count("security invoker set search_path = ''"), 5)
        self.assertIn("industrial banker requires matching user and company", SQL)
        self.assertIn("to service_role", SQL)
        self.assertNotIn("security definer", SQL.casefold())

    def test_world_market_is_only_money_source_here(self):
        self.assertEqual(SQL.count("credits=credits+total"), 1)
        self.assertNotIn("new_balance", SQL)


if __name__ == "__main__":
    unittest.main()
