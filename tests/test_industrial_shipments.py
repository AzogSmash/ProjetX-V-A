import pathlib
import unittest

from economy_v2.models import IngotShipment, ShipmentResult


ROOT = pathlib.Path(__file__).parents[1]
SQL = (ROOT / "supabase" / "029_industrial_ingot_shipments.sql").read_text(encoding="utf-8")


class ShipmentMigrationTests(unittest.TestCase):
    def test_escrow_schema_and_transitions(self):
        for token in (
            "industrial_ingot_shipments", "pending", "accepted", "cancelled",
            "blacksmith_discord_user_id", "merchant_discord_user_id",
            "banker_discord_user_id", "request_id text not null unique",
            "transport_id bigint unique", "resource_type = 'iron_ingot'",
        ):
            self.assertIn(token, SQL)

    def test_atomic_concurrency_guards(self):
        for token in (
            "pg_advisory_xact_lock", "for update", "industrial_inventory",
            "status = 'accepted'", "status = 'cancelled'",
            "request id reused with different shipment parameters",
            "accept_request_id", "cancel_request_id",
            "industrial_transports", "industrial_truck_capacity",
        ):
            self.assertIn(token, SQL)

    def test_rpc_security(self):
        self.assertIn("enable row level security", SQL)
        self.assertGreaterEqual(SQL.count("security invoker set search_path = ''"), 3)
        for role in ("public", "anon", "authenticated"):
            self.assertIn(role, SQL)
        self.assertIn("to service_role", SQL)
        self.assertNotIn("security definer", SQL.casefold())

    def test_result_model_keeps_single_location(self):
        shipment = IngotShipment(1, 10, 100, 20, 200, 30, 300,
                                 "iron_ingot", 25, "pending", "now")
        result = ShipmentResult(shipment)
        self.assertEqual(result.shipment.quantity, 25)
        self.assertIsNone(result.transport)


if __name__ == "__main__":
    unittest.main()
