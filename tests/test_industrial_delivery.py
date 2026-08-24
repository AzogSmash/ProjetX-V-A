import pathlib
import unittest

from economy_v2.delivery_config import (
    get_delivery_cooldown_seconds, get_delivery_level,
    get_delivery_reduction_seconds, get_delivery_xp, get_max_delivery_commission,
)


ROOT = pathlib.Path(__file__).parents[1]
SQL = (ROOT / "supabase" / "031_industrial_delivery.sql").read_text(encoding="utf-8")


class DeliveryConfigTests(unittest.TestCase):
    def test_formulas_are_bounded_and_deterministic(self):
        self.assertEqual(get_delivery_reduction_seconds(10), 1800)
        self.assertEqual(get_delivery_reduction_seconds(100), 1800)
        self.assertEqual(get_delivery_cooldown_seconds(1), 1800)
        self.assertEqual(get_delivery_cooldown_seconds(100), 300)
        self.assertEqual(get_delivery_level(0), 1)
        self.assertLessEqual(get_delivery_level(10**12), 100)
        self.assertEqual(get_delivery_xp(600), 30)
        self.assertEqual(get_max_delivery_commission(1), 20)
        self.assertEqual(get_max_delivery_commission(10**9), 5000)


class DeliveryMigrationTests(unittest.TestCase):
    def test_escrow_conservation(self):
        self.assertIn("commission_paid + merchant_refund = commission_max", SQL)
        self.assertIn("refunded:=m.commission_max-paid", SQL)
        self.assertIn("escrow_remaining=0", SQL)
        self.assertNotIn("new_balance", SQL)

    def test_departure_is_atomic_and_funded(self):
        self.assertEqual(SQL.count("insufficient_commission_funds"), 2)
        self.assertGreaterEqual(SQL.count("credits=credits-fee"), 2)
        self.assertGreaterEqual(SQL.count("industrial_delivery_missions"), 10)

    def test_acceptance_and_arrival_are_serialized(self):
        for token in ("for update", "already_taken", "arrival_at<=now_at",
                      "status='refunded'", "accept_request_id=p_request_id",
                      "pg_advisory_xact_lock"):
            self.assertIn(token, SQL)

    def test_security(self):
        self.assertIn("enable row level security", SQL)
        self.assertGreaterEqual(SQL.count("security invoker set search_path=''"), 8)
        self.assertIn("to service_role", SQL)
        self.assertNotIn("security definer", SQL.casefold())


if __name__ == "__main__":
    unittest.main()
