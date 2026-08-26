import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from economy_v2.commands import register_economy_commands
from economy_v2.commands.help import ecohelp_command
from economy_v2.commands.insights import build_fiche_command
from economy_v2.commands.next_actions import build_recommendations
from economy_v2.commands.tutorial import build_tutorial_command
from economy_v2.database import MIGRATIONS_DIRECTORY,connect_database,immediate_transaction,initialize_database_sync
from economy_v2.router import EconomyCommandContext,EconomyRouter
from economy_v2.services import SQLiteIndustrialEconomyService
from economy_v2.sqlite_repository import SQLiteIndustrialEconomyRepository


class TutorialCase(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/"tutorial.db";self.repo=SQLiteIndustrialEconomyRepository(self.path)
    def tearDown(self):self.tmp.cleanup()
    def start_role(self,uid,job):
        self.repo.create_first_company(uid,f"Company {uid}",job);self.repo.update_tutorial(uid,"start",f"start:{uid}");self.repo.update_tutorial(uid,"next",f"intro:{uid}");self.repo.update_tutorial(uid,"next",f"profile:{uid}");return self.repo.get_tutorial(uid)
    def economic_snapshot(self):
        with closing(connect_database(self.path)) as c:return tuple((table,int(c.execute(f"SELECT count(*) FROM {table}").fetchone()[0])) for table in ("industrial_users","industrial_companies","industrial_inventory","industrial_transactions","industrial_resource_events","industrial_market_orders","industrial_transports","industrial_forge_jobs","industrial_world_sales","industrial_achievements","industrial_reputation_events"))


class TutorialRepositoryTests(TutorialCase):
    def test_unknown_start_persists_without_creating_economic_profile(self):
        before=self.economic_snapshot();data=self.repo.update_tutorial(999,"start","unknown");self.assertEqual("common",data["path"]);self.assertEqual(before,self.economic_snapshot());self.assertEqual(0,self.repo.get_or_create_user(999).credits)

    def test_start_next_status_restart_stop_and_spam_are_idempotent(self):
        first=self.repo.update_tutorial(1,"start","s");duplicate=self.repo.update_tutorial(1,"start","s");self.assertEqual(first["current_step"],duplicate["current_step"]);blocked=self.repo.update_tutorial(1,"next","n");self.assertEqual(1,blocked["current_step"]);self.assertTrue(self.repo.update_tutorial(1,"next","blocked")["blocked"])
        stopped=self.repo.update_tutorial(1,"stop","stop");self.assertEqual("stopped",stopped["status"]);restarted=self.repo.update_tutorial(1,"restart","restart");self.assertEqual(("active",0),(restarted["status"],restarted["current_step"]))

    def test_miner_detects_collection_and_sale(self):
        data=self.start_role(1,"miner");self.assertEqual("mine",data["step"].slug);self.repo.update_tutorial(1,"next","mine-info")
        with immediate_transaction(self.path) as c:self.repo._add_inventory(c,1,"iron_ore",5)
        data=self.repo.update_tutorial(1,"next","collect");self.assertEqual("sell",data["step"].slug)
        with immediate_transaction(self.path) as c:
            aid=self.repo._actor_id(c,1);company=c.execute("SELECT id FROM industrial_companies WHERE owner_discord_user_id=1").fetchone()[0];c.execute("INSERT INTO industrial_market_orders(owner_actor_id,owner_discord_user_id,company_id,side,resource_type,original_quantity,remaining_quantity,unit_price,escrow_quantity,request_id) VALUES(?,1,?,'sell','iron_ore',1,1,8,1,'tutorial-sell')",(aid,company))
        self.assertEqual("miner_next",self.repo.update_tutorial(1,"next","sell")["step"].slug)

    def test_merchant_detects_buy_and_transport(self):
        data=self.start_role(2,"merchant");self.assertEqual("buy",data["step"].slug)
        with immediate_transaction(self.path) as c:
            aid=self.repo._actor_id(c,2);company=c.execute("SELECT id FROM industrial_companies WHERE owner_discord_user_id=2").fetchone()[0];c.execute("INSERT INTO industrial_market_orders(owner_actor_id,owner_discord_user_id,company_id,side,resource_type,original_quantity,remaining_quantity,unit_price,escrow_credits,request_id) VALUES(?,2,?,'buy','iron_ore',1,1,8,8,'tutorial-buy')",(aid,company))
        self.assertEqual("trucks",self.repo.update_tutorial(2,"next","buy")["step"].slug);self.repo.update_tutorial(2,"next","trucks")
        with immediate_transaction(self.path) as c:
            aid=self.repo._actor_id(c,2);company=c.execute("SELECT id FROM industrial_companies WHERE owner_discord_user_id=2").fetchone()[0];c.execute("INSERT INTO industrial_transports(sender_actor_id,receiver_actor_id,operator_actor_id,sender_company_id,receiver_company_id,merchant_discord_user_id,transport_type,resource_type,quantity,departure_at,arrival_at,original_duration_seconds,current_duration_seconds,truck_slot,request_id) VALUES(?,?,?,?,?,2,'ore_to_blacksmith','iron_ore',1,1,2,1,1,1,'tutorial-trip')",(aid,aid,aid,company,company))
        self.assertEqual("merchant_next",self.repo.update_tutorial(2,"next","transport")["step"].slug)

    def test_blacksmith_detects_forge_and_ingots(self):
        data=self.start_role(3,"blacksmith");self.assertEqual("ore",data["step"].slug);self.repo.update_tutorial(3,"next","ore")
        with immediate_transaction(self.path) as c:
            company=c.execute("SELECT id FROM industrial_companies WHERE owner_discord_user_id=3").fetchone()[0];c.execute("INSERT INTO industrial_forge_jobs(owner_discord_user_id,company_id,forge_slot,input_quantity,output_quantity,speed_level_at_start,yield_level_at_start,started_at,finishes_at,status,request_id) VALUES(3,?,1,1,1,1,1,1,2,'collected','tutorial-forge')",(company,));self.repo._add_inventory(c,3,"iron_ingot",1)
        self.assertEqual("forge_next",self.repo.update_tutorial(3,"next","forge")["step"].slug)

    def test_banker_detects_inventory_and_world_sale(self):
        data=self.start_role(4,"banker");self.assertEqual("world_market",data["step"].slug);self.repo.update_tutorial(4,"next","market")
        with immediate_transaction(self.path) as c:self.repo._add_inventory(c,4,"iron_ingot",2)
        data=self.repo.update_tutorial(4,"next","ingots");self.assertEqual("world_sale",data["step"].slug)
        with immediate_transaction(self.path) as c:
            company=c.execute("SELECT id FROM industrial_companies WHERE owner_discord_user_id=4").fetchone()[0];c.execute("INSERT INTO industrial_world_sales(banker_discord_user_id,banker_company_id,quantity,unit_price,total_credits,balance_after,request_id) VALUES(4,?,1,80,80,80,'tutorial-world')",(company,))
        self.assertEqual("bank_next",self.repo.update_tutorial(4,"next","sale")["step"].slug)

    def test_experienced_player_skips_verified_steps_and_job_change_recalculates(self):
        self.repo.create_first_company(5,"Veteran","miner")
        with immediate_transaction(self.path) as c:
            self.repo._add_inventory(c,5,"iron_ore",5);aid=self.repo._actor_id(c,5);company=c.execute("SELECT id FROM industrial_companies WHERE owner_discord_user_id=5").fetchone()[0];c.execute("INSERT INTO industrial_market_orders(owner_actor_id,owner_discord_user_id,company_id,side,resource_type,original_quantity,remaining_quantity,unit_price,escrow_quantity,request_id) VALUES(?,5,?,'sell','iron_ore',1,1,8,1,'veteran')",(aid,company))
        self.repo.update_tutorial(5,"start","s5");self.repo.update_tutorial(5,"next","i5");self.repo.update_tutorial(5,"next","p5");self.repo.update_tutorial(5,"next","m5");self.assertEqual("miner_next",self.repo.get_tutorial(5)["step"].slug)
        with immediate_transaction(self.path) as c:c.execute("UPDATE industrial_users SET primary_job='banker' WHERE discord_user_id=5")
        self.assertEqual("banker",self.repo.get_tutorial(5)["path"])

    def test_progress_survives_restart_and_completion_is_concurrent_safe(self):
        self.start_role(6,"miner");restarted=SQLiteIndustrialEconomyRepository(self.path);self.assertEqual(self.repo.get_tutorial(6)["current_step"],restarted.get_tutorial(6)["current_step"])
        with immediate_transaction(self.path) as c:c.execute("UPDATE industrial_tutorial_progress SET current_step=7 WHERE discord_user_id=6")
        repos=(self.repo,restarted)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda pair:pair[1].update_tutorial(6,"next",pair[0]),(("done-a",repos[0]),("done-b",repos[1]))))
        self.assertTrue(all(r["status"]=="completed" for r in results));self.assertEqual(0,self.repo.get_or_create_user(6).credits)

    def test_concurrent_restart_has_one_coherent_state(self):
        self.start_role(8,"miner");repositories=(self.repo,SQLiteIndustrialEconomyRepository(self.path))
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda pair:pair[1].update_tutorial(8,"restart",pair[0]),(("restart-a",repositories[0]),("restart-b",repositories[1]))))
        self.assertTrue(all((r["status"],r["current_step"])==("active",0) for r in results));self.assertEqual(0,self.repo.get_tutorial(8)["current_step"])

    def test_next_snapshot_is_read_only_and_contains_active_tutorial(self):
        self.repo.update_tutorial(7,"start","s7");before=self.economic_snapshot();snapshot=self.repo.get_next_actions_snapshot(7);self.assertEqual(before,self.economic_snapshot());self.assertEqual("active",snapshot["tutorial"]["status"])
        from tests.test_industrial_next_actions import base_snapshot
        recs=build_recommendations(base_snapshot(tutorial=snapshot["tutorial"]));self.assertIn("?tutorial",[r[3] for r in recs])


class TutorialCommandTests(TutorialCase,unittest.IsolatedAsyncioTestCase):
    class Channel:
        def __init__(self):self.sent=[]
        async def send(self,content=None,**kwargs):self.sent.append((content,kwargs))
    def message(self,mid=1,uid=10):return SimpleNamespace(id=mid,author=SimpleNamespace(id=uid),channel=self.Channel())

    async def test_command_subcommands_french_aliases_and_embed_limits(self):
        service=SQLiteIndustrialEconomyService(self.repo);message=self.message()
        for i,args in enumerate((("start",),("statut",),("suivant",),("stop",),("recommencer",)),1):
            message.id=i;message.channel.sent.clear();await build_tutorial_command(service)(EconomyCommandContext(message,args,None));embed=message.channel.sent[-1][1]["embed"];self.assertLessEqual(len(embed.fields),25);self.assertTrue(all(len(f.value)<=1024 for f in embed.fields));self.assertLessEqual(len(embed),6000)

    async def test_fiche_and_help_integrations(self):
        self.repo.create_first_company(11,"Profile Co","miner");self.repo.update_tutorial(11,"start","profile-start");service=SQLiteIndustrialEconomyService(self.repo);message=self.message(uid=11)
        await build_fiche_command(service)(EconomyCommandContext(message,(),None));self.assertTrue(any(f.name=="Tutoriel" for f in message.channel.sent[-1][1]["embed"].fields))
        help_message=self.message(uid=11);await ecohelp_command(EconomyCommandContext(help_message,(),None));self.assertIn("?tutorial"," ".join(f.value for f in help_message.channel.sent[0][1]["embed"].fields))

    async def test_registry_aliases_no_tracking_and_no_collision(self):
        router=EconomyRouter();register_economy_commands(router,SimpleNamespace());self.assertTrue({"tutorial","tuto"}<=router.command_names);self.assertTrue({"tutorial","tuto"}<=router._activity_exempt_commands)


class TutorialMigrationTests(unittest.TestCase):
    def test_upgrade_from_006_preserves_data_restart_and_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"v6.db";c=connect_database(path);c.execute("CREATE TABLE industrial_schema_version(version INTEGER PRIMARY KEY,applied_at INTEGER NOT NULL)")
            for migration in sorted(MIGRATIONS_DIRECTORY.glob("00[1-6]_*.sql")):
                version=int(migration.name[:3]);c.executescript("BEGIN IMMEDIATE;\n"+migration.read_text(encoding="utf-8")+f"\nINSERT INTO industrial_schema_version VALUES({version},unixepoch());\nCOMMIT;")
            c.execute("INSERT INTO industrial_users(discord_user_id,credits) VALUES(77,777)");c.close();initialize_database_sync(path);initialize_database_sync(path)
            with closing(connect_database(path)) as c:self.assertEqual(777,c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=77").fetchone()[0]);self.assertEqual([1,2,3,4,5,6,7],[r[0] for r in c.execute("SELECT version FROM industrial_schema_version ORDER BY version")]);self.assertEqual("ok",c.execute("PRAGMA integrity_check").fetchone()[0]);self.assertEqual([],c.execute("PRAGMA foreign_key_check").fetchall())
