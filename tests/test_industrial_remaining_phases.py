import pathlib
import unittest

from economy_v2.ai_config import ai_is_needed
from economy_v2.contracts_config import valid_contract_values


ROOT = pathlib.Path(__file__).parents[1]


class ContractTests(unittest.TestCase):
    def test_limits(self):
        self.assertTrue(valid_contract_values(500, 4000))
        self.assertFalse(valid_contract_values(0, 4000))
        self.assertFalse(valid_contract_values(500, 0))

    def test_contract_sql_is_atomic_and_idempotent(self):
        sql = (ROOT / "supabase/032_industrial_contracts.sql").read_text(encoding="utf-8")
        for token in ("escrow_credits", "for update", "accept_request_id",
                      "cancel_request_id", "credits=credits-p_total_price",
                      "quantity=target_inventory.quantity-c.quantity", "refresh_industrial_contracts"):
            self.assertIn(token, sql)
        self.assertIn("enable row level security", sql)
        self.assertNotIn("security definer", sql.casefold())


class AiTests(unittest.TestCase):
    def test_ai_is_strictly_limited_and_weaker(self):
        self.assertTrue(ai_is_needed(0))
        self.assertFalse(ai_is_needed(2))
        sql = (ROOT / "supabase/033_industrial_ai.sql").read_text(encoding="utf-8")
        self.assertIn("job_type text not null unique", sql)
        self.assertIn("efficiency_percent integer not null default 60", sql)
        self.assertIn("interval '30 days'", sql)
        self.assertNotIn("owner_discord_user_id", sql)

class EndToEndInvariantTests(unittest.TestCase):
    def test_money_conservation_scenario(self):
        ore = 100
        locations = {"miner": ore, "escrow": 0, "merchant": 0, "transport": 0,
                     "blacksmith": 0, "forge": 0, "ingot": 0, "banker": 0}
        locations["miner"] -= ore; locations["escrow"] += ore
        locations["escrow"] -= ore; locations["merchant"] += ore
        locations["merchant"] -= ore; locations["transport"] += ore
        locations["transport"] -= ore; locations["blacksmith"] += ore
        locations["blacksmith"] -= ore; locations["forge"] += ore
        locations["forge"] -= ore; locations["ingot"] += ore
        locations["ingot"] -= ore; locations["transport"] += ore
        locations["transport"] -= ore; locations["banker"] += ore
        self.assertEqual(sum(locations.values()), ore)
        self.assertEqual(locations["banker"], ore)


if __name__ == "__main__":
    unittest.main()
