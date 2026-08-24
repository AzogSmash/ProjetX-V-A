import pathlib
import unittest

SQL=(pathlib.Path(__file__).parents[1]/"supabase/038_industrial_security_audit.sql").read_text(encoding="utf-8")

class SecurityAuditTests(unittest.TestCase):
 def test_mine_upgrade_is_idempotent(self):
  for token in ("industrial_mine_upgrade_requests","p_request_id","request id parameter mismatch",
                "pg_advisory_xact_lock","upgrade_industrial_mine_idempotent"):
   self.assertIn(token,SQL)
 def test_money_flows_are_classified(self):
  for token in ("'source'","'transfer'","'sink'","world_sale","ai_bootstrap",
                "market_trade","delivery_commission","contract_completion","mine_upgrade"):
   self.assertIn(token,SQL)
 def test_audit_is_append_only_and_private(self):
  self.assertIn("enable row level security",SQL)
  self.assertIn("grant select,insert",SQL)
  self.assertNotIn("grant update",SQL)
  self.assertNotIn("security definer",SQL.casefold())

if __name__=="__main__":unittest.main()
