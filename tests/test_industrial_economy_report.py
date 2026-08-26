import inspect
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from economy_v2.commands.economy_report import build_economy_report_command,build_report_embeds,build_report_text
from economy_v2.commands import register_economy_commands
from economy_v2.database import connect_database,immediate_transaction
from economy_v2.reporting_repository import BALANCE_ALERT_THRESHOLDS
from economy_v2.router import EconomyCommandContext,EconomyRouter
from economy_v2.services import SQLiteIndustrialEconomyService
from economy_v2.sqlite_repository import SQLiteIndustrialEconomyRepository


class EconomyReportCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/"report.db";self.repo=SQLiteIndustrialEconomyRepository(self.path);self.now=int(time.time())
    def tearDown(self):self.tmp.cleanup()
    def dump(self):
        with closing(connect_database(self.path)) as c:return "\n".join(c.iterdump())
    def seed(self):
        for uid,name,job in ((1,"Mine One","miner"),(2,"Trade Two","merchant"),(3,"Forge Three","blacksmith"),(4,"Bank Four","banker")):
            self.repo.create_first_company(uid,name,job)
        self.repo.adjust_admin_credits(99,1,"add",700,"seed-admin");self.repo.adjust_admin_credits(99,2,"add",200,"seed-merchant")
        with immediate_transaction(self.path) as c:
            actors={int(r[1]):int(r[0]) for r in c.execute("SELECT id,discord_user_id FROM industrial_actors WHERE discord_user_id IS NOT NULL")};companies={int(r[1]):int(r[0]) for r in c.execute("SELECT id,owner_discord_user_id FROM industrial_companies")}
            c.execute("INSERT INTO industrial_ai_companies(name,job_type,enabled) VALUES('AI Mine','miner',1)");ai_company=c.execute("SELECT last_insert_rowid()").fetchone()[0];c.execute("INSERT INTO industrial_actors(actor_type,ai_company_id) VALUES('ai',?)",(ai_company,));ai=c.execute("SELECT last_insert_rowid()").fetchone()[0];c.execute("INSERT INTO industrial_ai_accounts(actor_id,credits) VALUES(?,300)",(ai,))
            for actor,kind,resource,qty,created in ((actors[1],"player","iron_ore",100,self.now-1000),(actors[3],"player","iron_ingot",40,self.now-1000),(ai,"ai","iron_ore",80,self.now-1000),(ai,"ai","iron_ingot",10,self.now-90000)):
                c.execute("INSERT INTO industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity,created_at) VALUES(?,?,'mine_production',?,?,?)",(actor,kind,resource,qty,created))
            c.execute("INSERT INTO industrial_market_orders(owner_actor_id,owner_discord_user_id,company_id,side,resource_type,original_quantity,remaining_quantity,unit_price,escrow_quantity,request_id) VALUES(?,?,?,'buy','iron_ore',10,10,8,0,'buy')",(actors[2],2,companies[2]));buy=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO industrial_market_orders(owner_actor_id,owner_discord_user_id,company_id,side,resource_type,original_quantity,remaining_quantity,unit_price,escrow_quantity,request_id) VALUES(?,?,?,'sell','iron_ore',10,10,12,10,'sell')",(actors[1],1,companies[1]));sell=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO industrial_market_trades(resource_type,quantity,unit_price,total_price,seller_actor_id,buyer_actor_id,seller_discord_user_id,buyer_discord_user_id,sell_order_id,buy_order_id,created_at) VALUES('iron_ore',20,10,200,?,?,?,?,?,?,?)",(actors[1],actors[2],1,2,sell,buy,self.now-1000))
            c.execute("INSERT INTO industrial_world_sales(banker_discord_user_id,banker_company_id,quantity,unit_price,total_credits,balance_after,request_id,created_at) VALUES(4,?,5,80,400,400,'world',?)",(companies[4],self.now-1000));wid=c.execute("SELECT last_insert_rowid()").fetchone()[0];c.execute("INSERT INTO industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id,created_at) VALUES('world_sale','source',?,400,'world_sale',?,?)",(actors[4],wid,self.now-1000))
            c.execute("INSERT INTO industrial_transports(sender_actor_id,receiver_actor_id,operator_actor_id,sender_company_id,receiver_company_id,merchant_discord_user_id,transport_type,resource_type,quantity,departure_at,arrival_at,original_duration_seconds,current_duration_seconds,status,truck_slot,request_id,created_at) VALUES(?,?,?,?,?,2,'ore_to_blacksmith','iron_ore',20,?,?,3600,3000,'in_transit',1,'transport',?)",(actors[1],actors[3],actors[2],companies[1],companies[3],self.now-1000,self.now+2000,self.now-1000));transport=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO industrial_delivery_missions(transport_id,merchant_actor_id,merchant_discord_user_id,resource_type,quantity,status,commission_max,escrow_remaining,courier_discord_user_id,commission_paid,saved_seconds,accept_request_id,accepted_at) VALUES(?,?,2,'iron_ore',20,'accepted',100,0,1,60,600,'delivery',?)",(transport,actors[2],self.now-500));c.execute("INSERT INTO industrial_delivery_profiles(discord_user_id,delivery_level,completed_deliveries) VALUES(1,3,1)")
            c.execute("INSERT INTO industrial_contracts(creator_discord_user_id,accepter_discord_user_id,resource_type,quantity,total_price,escrow_credits,status,request_id,created_at,completed_at) VALUES(2,1,'iron_ore',10,100,0,'completed','done-contract',?,?)",(self.now-500,self.now-400));c.execute("INSERT INTO industrial_contracts(creator_discord_user_id,resource_type,quantity,total_price,escrow_credits,status,request_id,created_at,expires_at) VALUES(2,'iron_ore',10,100,100,'open','open-contract',?,?)",(self.now-500,self.now+5000));c.execute("INSERT INTO industrial_contracts(creator_discord_user_id,resource_type,quantity,total_price,escrow_credits,status,request_id,created_at,expires_at) VALUES(2,'iron_ore',10,100,0,'expired','expired-contract',?,?)",(self.now-500,self.now-1))
            c.execute("INSERT INTO industrial_achievements(discord_user_id,achievement_key,title,earned_at) VALUES(1,'report','Report',?)",(self.now-100,));c.execute("INSERT INTO industrial_objective_progress(discord_user_id,objective_key,period_type,period_start,target,progress,completed_at) VALUES(1,'report','daily',?,1,1,?)",(self.now-self.now%86400,self.now-100));c.execute("INSERT INTO industrial_reputation_events(discord_user_id,event_type,reputation,source_key) VALUES(1,'test',20,'report')")
            c.execute("INSERT INTO industrial_seasons(season_number,name,starts_at,ends_at,status) VALUES(1,'Saison test',?,?,'active')",(self.now-1000,self.now+5000));season=c.execute("SELECT last_insert_rowid()").fetchone()[0];c.execute("INSERT INTO industrial_season_scores(season_id,actor_id,discord_user_id,category,score) VALUES(?,?,1,'overall',123)",(season,actors[1]));c.execute("INSERT INTO industrial_season_scores(season_id,actor_id,discord_user_id,category,score) VALUES(?,?,1,'mine',100)",(season,actors[1]))
            cycle=self.now//21600;c.execute("INSERT INTO industrial_economic_events(cycle_key,event_type,display_name,starts_at,ends_at,multiplier_bps,status) VALUES(?,'mining_rush','Ruée',?,?,11500,'active')",(f"report:{cycle}",self.now-10,self.now+1000))
            c.execute("UPDATE industrial_transactions SET created_at=?",(self.now-1000,))


class EconomyReportRepositoryTests(EconomyReportCase):
    def test_empty_database_report_is_complete_and_read_only(self):
        before=self.dump();r=self.repo.get_economy_report(self.now);self.assertEqual(before,self.dump());self.assertEqual(0,r["global"]["players"]);self.assertEqual(0,r["wealth"]["median"]);self.assertIsNone(r["season"])

    def test_players_ai_money_jobs_and_time_windows(self):
        self.seed();r=self.repo.get_economy_report(self.now);self.assertEqual((4,4,900,300),(r["global"]["players"],r["global"]["companies"],r["global"]["player_cr"],r["global"]["ai_cr"]));self.assertEqual(1300,r["global"]["day"]["created"]);self.assertEqual(1,r["jobs"]["players"]["miner"]);self.assertEqual(1,r["jobs"]["ai"]["miner"])

    def test_production_market_and_bank_metrics(self):
        self.seed();r=self.repo.get_economy_report(self.now);self.assertEqual(180,r["production"]["day"]["ore"]["total"]);self.assertEqual(50,r["production"]["week"]["ingots"]["total"]);self.assertEqual((8,12,4),(r["market"]["best_buy"],r["market"]["best_sell"],r["market"]["spread"]));self.assertEqual(10,r["market"]["day"]["average"]);self.assertEqual((80,5,400),(r["world"]["current_price"],r["world"]["day"]["volume"],r["world"]["day"]["credits"]))

    def test_logistics_deliveries_contracts(self):
        self.seed();r=self.repo.get_economy_report(self.now);self.assertEqual(1,r["logistics"]["active"]);self.assertEqual(1,r["delivery"]["week"]["completed"]);self.assertEqual(600,r["delivery"]["week"]["saved_seconds"]);self.assertEqual(1,r["contracts"]["open"]);self.assertEqual(100,r["contracts"]["escrow"]);self.assertEqual((1,1),(r["contracts"]["week"]["completed"],r["contracts"]["week"]["expired"]))

    def test_wealth_progression_season_events_and_alerts(self):
        self.seed();r=self.repo.get_economy_report(self.now);self.assertEqual(100,r["wealth"]["median"]);self.assertAlmostEqual(225,r["wealth"]["average"]);self.assertAlmostEqual(700/900*100,r["wealth"]["top_decile_share"]);self.assertEqual(1,r["progression"]["achievements_24h"]);self.assertEqual("Saison test",r["season"]["name"]);self.assertEqual(123,r["season"]["top_score"]);self.assertEqual(11500,r["events"][0]["multiplier_bps"]);self.assertTrue(r["alerts"])

    def test_report_is_read_only_on_populated_database_and_reasonably_fast(self):
        self.seed();before=self.dump();start=time.perf_counter();self.repo.get_economy_report(self.now);elapsed=time.perf_counter()-start;self.assertEqual(before,self.dump());self.assertLess(elapsed,2.0)

    def test_thresholds_are_centralized_and_documented(self):
        self.assertEqual(40.0,BALANCE_ALERT_THRESHOLDS["ai_production_percent"]);self.assertEqual(70.0,BALANCE_ALERT_THRESHOLDS["top_decile_wealth_percent"]);self.assertEqual(50.0,BALANCE_ALERT_THRESHOLDS["market_spread_percent"])


class EconomyReportCommandTests(EconomyReportCase,unittest.IsolatedAsyncioTestCase):
    class Channel:
        def __init__(self):self.sent=[]
        async def send(self,content=None,**kwargs):self.sent.append((content,kwargs))
    def message(self,admin=True):return SimpleNamespace(author=SimpleNamespace(id=99,guild_permissions=SimpleNamespace(administrator=admin)),channel=self.Channel(),id=123)

    async def test_non_admin_is_refused_without_repository_access(self):
        class Service:
            async def get_economy_report(self):raise AssertionError("must not be called")
        message=self.message(False);await build_economy_report_command(Service())(EconomyCommandContext(message,(),None));self.assertIn("permission",message.channel.sent[0][0])

    async def test_admin_embed_and_text_modes_respect_discord_limits(self):
        self.seed();service=SQLiteIndustrialEconomyService(self.repo)
        message=self.message();before=self.dump();await build_economy_report_command(service)(EconomyCommandContext(message,(),None));self.assertEqual(before,self.dump());self.assertEqual(4,len(message.channel.sent))
        for _,kwargs in message.channel.sent:
            embed=kwargs["embed"];self.assertLessEqual(len(embed.fields),25);self.assertTrue(all(len(f.value)<=1024 for f in embed.fields));self.assertLessEqual(len(embed),6000)
        text_message=self.message();await build_economy_report_command(service)(EconomyCommandContext(text_message,("text",),None));self.assertTrue(all(len(content)<=1920 for content,_ in text_message.channel.sent));self.assertIn("ECONOMY REPORT","".join(x[0] for x in text_message.channel.sent))

    async def test_registry_disables_activity_tracking_for_both_names(self):
        calls=[];router=EconomyRouter(lambda uid:calls.append(uid));register_economy_commands(router,SimpleNamespace())
        self.assertIn("economyreport",router.command_names);self.assertIn("ereport",router.command_names);self.assertTrue({"economyreport","ereport"}<=router._activity_exempt_commands)

    def test_no_old_economy_or_supabase_dependency(self):
        import economy_v2.reporting_repository as repository_module
        import economy_v2.commands.economy_report as command_module
        source=inspect.getsource(repository_module)+inspect.getsource(command_module);self.assertNotIn("supabase",source.casefold());self.assertNotIn("data.json",source.casefold());self.assertNotIn("coins",source.casefold())

    def test_text_renderer_is_compact(self):
        self.seed();text=build_report_text(self.repo.get_economy_report(self.now));self.assertLess(len(text),5000);self.assertIn("ALERTS",text)
