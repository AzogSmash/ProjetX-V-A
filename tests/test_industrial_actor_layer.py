import pathlib
import unittest


SQL = (pathlib.Path(__file__).parents[1] / "supabase/034_industrial_actor_layer.sql").read_text(encoding="utf-8")


class ActorLayerMigrationTests(unittest.TestCase):
    def test_actor_identity_has_no_fake_discord_ids(self):
        for token in ("actor_type in('player','ai')", "discord_user_id is not null",
                      "ai_company_id is not null", "unique(discord_user_id)",
                      "unique(ai_company_id)"):
            self.assertIn(token, SQL)

    def test_inventory_backfill_is_guarded(self):
        self.assertIn("actor inventory backfill incomplete", SQL)
        self.assertIn("actor inventory duplicates", SQL)
        self.assertIn("primary key(actor_id,resource_type)", SQL)
        self.assertIn("industrial_inventory_actor_resource_pkey", SQL)
        self.assertIn("industrial_inventory_enforce_actor", SQL)

    def test_transport_backfill_and_compatibility(self):
        for token in ("sender_actor_id", "receiver_actor_id", "operator_actor_id",
                      "actor transport backfill incomplete", "industrial_transports_enforce_actors",
                      "industrial_transports_active_actor_truck_idx"):
            self.assertIn(token, SQL)

    def test_rpc_security(self):
        self.assertEqual(SQL.count("security invoker set search_path=''"), 2)
        self.assertIn("enable row level security", SQL)
        self.assertIn("to service_role", SQL)
        self.assertNotIn("security definer", SQL.casefold())


if __name__ == "__main__":
    unittest.main()
