import pathlib
import unittest

from economy_v2.ai_economy_config import get_ai_unit_price


SQL = (pathlib.Path(__file__).parents[1] / "supabase/036_industrial_ai_economy.sql").read_text(encoding="utf-8")


class AiEconomyTests(unittest.TestCase):
    def test_prices_are_explicit_and_less_attractive(self):
        self.assertEqual(get_ai_unit_price("iron_ore"), 12)
        self.assertEqual(get_ai_unit_price("iron_ingot"), 100)
        with self.assertRaises(ValueError): get_ai_unit_price("gold")

    def test_budget_sources_and_transfers_are_auditable(self):
        for token in ("industrial_ai_accounts", "industrial_ai_cash_events",
                      "bootstrap_source", "player_payment_transfer", "delivery_escrow",
                      "delivery_refund", "25000"):
            self.assertIn(token, SQL)
        self.assertNotIn("new_balance", SQL)

    def test_ai_is_lazy_and_uses_shared_engines(self):
        for token in ("refresh_industrial_ai_production", "industrial_inventory",
                      "industrial_transports", "industrial_delivery_missions",
                      "industrial_market_trades", "production_progress"):
            self.assertIn(token, SQL)

    def test_purchase_is_atomic_idempotent_and_actor_locked(self):
        for token in ("pg_advisory_xact_lock(-lock_actor)", "request_id text not null unique",
                      "for update", "insufficient_funds", "insufficient_ai_stock",
                      "ai_truck_busy", "'duplicate'"):
            self.assertIn(token, SQL)
        self.assertIn("request id parameter mismatch", SQL)
        self.assertIn("purchase.resource_type<>p_resource_type", SQL)
        self.assertIn("purchase.quantity<>p_quantity", SQL)

    def test_security(self):
        self.assertEqual(SQL.count("security invoker set search_path=''"), 3)
        self.assertGreaterEqual(SQL.count("enable row level security"), 4)
        self.assertIn("to service_role", SQL)
        self.assertNotIn("security definer", SQL.casefold())


if __name__ == "__main__":
    unittest.main()
