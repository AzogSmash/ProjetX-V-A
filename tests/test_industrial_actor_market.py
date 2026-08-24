import pathlib
import unittest


SQL = (pathlib.Path(__file__).parents[1] / "supabase/035_industrial_actor_market.sql").read_text(encoding="utf-8")


class ActorMarketMigrationTests(unittest.TestCase):
    def test_orders_and_trades_are_backfilled(self):
        for token in ("owner_actor_id", "seller_actor_id", "buyer_actor_id",
                      "market order actor backfill incomplete", "market trade actor backfill incomplete"):
            self.assertIn(token, SQL)

    def test_legacy_columns_remain_but_allow_ai(self):
        self.assertIn("owner_discord_user_id drop not null", SQL)
        self.assertIn("seller_discord_user_id drop not null", SQL)
        self.assertIn("buyer_discord_user_id drop not null", SQL)
        self.assertNotIn("drop column", SQL)

    def test_actor_consistency_triggers(self):
        self.assertIn("industrial_market_orders_enforce_actor", SQL)
        self.assertIn("industrial_market_trades_enforce_actors", SQL)
        self.assertIn("seller_actor_id<>buyer_actor_id", SQL)


if __name__ == "__main__":
    unittest.main()
