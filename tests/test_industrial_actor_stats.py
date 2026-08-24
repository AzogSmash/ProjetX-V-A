import pathlib
import unittest

SQL=(pathlib.Path(__file__).parents[1]/"supabase/037_industrial_economy_stats.sql").read_text(encoding="utf-8")

class ActorStatsTests(unittest.TestCase):
 def test_stats_distinguish_player_ai_and_total(self):
  for token in ("player_credits","ai_credits","player_ore","ai_ore","player_ingots","ai_ingots",
                "ai_ore_percent","ai_ingot_percent","ai_market_percent","ai_transport_percent",
                "market_volume","market_average_price","average_delivery_minutes",
                "world_price_change_percent","active_player_companies","active_ai_companies"):
   self.assertIn(token,SQL)
 def test_production_uses_persisted_events(self):
  self.assertIn("industrial_resource_events",SQL)
  self.assertIn("new.total_produced-old.total_produced",SQL)
  self.assertIn("new.stock-old.stock",SQL)
 def test_security(self):
  self.assertIn("security invoker set search_path=''",SQL)
  self.assertIn("enable row level security",SQL)
  self.assertNotIn("security definer",SQL.casefold())

if __name__=="__main__":unittest.main()
