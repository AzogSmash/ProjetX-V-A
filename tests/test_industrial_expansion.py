import asyncio
import ast
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from economy_v2 import build_economy_router
from economy_v2.backups import backup_once
import economy_v2.backups as backups
from economy_v2.commands.next_actions import build_recommendations
from economy_v2.commands.insights import build_economycheck_command, build_fiche_command
from economy_v2.router import EconomyCommandContext
from economy_v2.database import MIGRATIONS_DIRECTORY, connect_database, immediate_transaction, initialize_database_sync
from economy_v2.progression import company_size, company_value
from economy_v2.services import SQLiteIndustrialEconomyService
from economy_v2.sqlite_repository import SQLiteIndustrialEconomyRepository


class ExpansionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/"industrial.db";self.repo=SQLiteIndustrialEconomyRepository(self.path)
    def tearDown(self):self.tmp.cleanup()
    def company(self,uid,name,job):
        self.repo.create_first_company(uid,name,job);return self.repo.get_primary_company(uid)

    def test_profile_unknown_is_read_only(self):
        self.assertIsNone(self.repo.get_industrial_profile(999))
        with closing(connect_database(self.path)) as c:self.assertEqual(0,c.execute("SELECT count(*) FROM industrial_users").fetchone()[0])

    def test_value_formula_is_deterministic_and_non_cashable(self):
        data={"credits":100,"inventory":{"iron_ore":10},"infrastructure":{},"market_volume":999999}
        self.assertEqual(180,company_value(data));self.assertEqual(company_value(data),company_value(data));self.assertEqual("Petite entreprise",company_size(180))

    def test_rankings_and_stats_use_real_sqlite_events(self):
        self.repo.adjust_admin_credits(9,1,"add",1000,"seed")
        self.repo.adjust_admin_credits(9,2,"add",500,"seed2")
        self.assertEqual(1,self.repo.get_rankings("money")[0]["discord_user_id"])
        stats=self.repo.get_player_stats(1);self.assertEqual(1000,stats["credits"]);self.assertEqual(1000,stats["credits_gained"])

    def test_achievements_are_persistent_idempotent_and_non_monetary(self):
        self.repo.adjust_admin_credits(9,1,"add",1_000_000,"seed")
        before=self.repo.get_or_create_user(1).credits;first=self.repo.refresh_achievements(1);second=self.repo.refresh_achievements(1)
        self.assertEqual(first,second);self.assertEqual(before,self.repo.get_or_create_user(1).credits)
        with closing(connect_database(self.path)) as c:self.assertEqual(len(first),c.execute("SELECT count(*) FROM industrial_achievements WHERE discord_user_id=1").fetchone()[0])

    def test_objectives_periods_are_utc_and_idempotent(self):
        self.repo.get_or_create_user(1);a=self.repo.get_objectives(1,1_800_000_000);b=self.repo.get_objectives(1,1_800_000_000)
        self.assertEqual(a,b)
        with closing(connect_database(self.path)) as c:self.assertEqual(len(a),c.execute("SELECT count(*) FROM industrial_objective_progress").fetchone()[0])
        later=self.repo.get_objectives(1,1_800_086_400);self.assertNotEqual(a[0]["period_start"],later[0]["period_start"])

    def test_partnership_requires_consent_and_compatible_jobs(self):
        self.company(1,"Mine One","miner");self.company(2,"Trade Two","merchant")
        self.assertEqual("pending",self.repo.update_partnership(1,2,"add","p1")["status"])
        self.assertEqual("accepted",self.repo.update_partnership(2,1,"add","p2")["status"])
        self.assertEqual("accepted",self.repo.get_partnerships(1)[0]["status"])

    def test_targeted_contract_escrow_and_visibility(self):
        self.company(1,"Forge One","blacksmith");self.company(2,"Mine Two","miner");self.company(3,"Mine Three","miner")
        self.repo.adjust_admin_credits(9,1,"add",7000,"cash")
        status,_,contract=self.repo.create_contract(1,"iron_ore",500,7000,"contract",2);self.assertEqual("ok",status)
        self.assertEqual([contract.id],[x.id for x in self.repo.get_contracts(2,False)]);self.assertEqual([],self.repo.get_contracts(3,False))
        with immediate_transaction(self.path) as c:self.repo._add_inventory(c,2,"iron_ore",500)
        self.assertEqual("ok",self.repo.accept_contract(2,contract.id,"accept")[0]);self.assertEqual(7000,self.repo.get_or_create_user(2).credits)

    def test_notifications_and_event_idempotence(self):
        self.repo.set_notification_preference(1,"market",False);self.assertEqual(0,self.repo.get_notification_preferences(1)["market_enabled"])
        self.assertTrue(self.repo.enqueue_notification(1,"market","trade:1"));self.assertFalse(self.repo.enqueue_notification(1,"market","trade:1"))

    def test_economy_check_is_read_only_and_clean(self):
        self.repo.get_or_create_user(1)
        before=self.path.stat().st_size;result=self.repo.economy_check();self.assertEqual("ok",result["integrity"]);self.assertTrue(all(v==0 for v in result["checks"].values()));self.assertEqual(before,self.path.stat().st_size)

    def test_concurrent_progression_has_no_duplicate_award(self):
        self.repo.adjust_admin_credits(9,1,"add",1_000_000,"seed")
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:self.repo.refresh_achievements(1),range(2)))
        with closing(connect_database(self.path)) as c:self.assertEqual(1,c.execute("SELECT count(*) FROM industrial_achievements WHERE discord_user_id=1 AND achievement_key='millionaire'").fetchone()[0])


class MigrationAndBackupTests(unittest.TestCase):
    def test_upgrade_from_002_preserves_data_and_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"old.db";c=connect_database(path);c.execute("CREATE TABLE industrial_schema_version(version INTEGER PRIMARY KEY,applied_at INTEGER NOT NULL)")
            for migration in sorted(MIGRATIONS_DIRECTORY.glob("00[12]_*.sql")):
                version=int(migration.name[:3]);c.executescript("BEGIN IMMEDIATE;\n"+migration.read_text(encoding="utf-8")+f"\nINSERT INTO industrial_schema_version VALUES({version},unixepoch());\nCOMMIT;")
            c.execute("INSERT INTO industrial_users(discord_user_id,credits) VALUES(42,1234)");c.close();initialize_database_sync(path);initialize_database_sync(path)
            with closing(connect_database(path)) as c:
                self.assertEqual(1234,c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=42").fetchone()[0]);self.assertEqual([1,2,3,4,5],[r[0] for r in c.execute("SELECT version FROM industrial_schema_version ORDER BY version")]);self.assertEqual("ok",c.execute("PRAGMA integrity_check").fetchone()[0]);self.assertEqual([],c.execute("PRAGMA foreign_key_check").fetchall())

    def test_backup_rotation_and_readability(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/"source.db";SQLiteIndustrialEconomyRepository(source).get_or_create_user(7);dest=root/"backups"
            for stamp in (100,101,102):backup_once(source,dest,retention=2,now=stamp)
            files=list(dest.glob("industrial_economy-*.db"));self.assertEqual(2,len(files))
            with closing(sqlite3.connect(files[0])) as c:self.assertEqual("ok",c.execute("PRAGMA integrity_check").fetchone()[0])

    def test_002_representative_industrial_rows_survive_005(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"production.db";c=connect_database(path);c.execute("CREATE TABLE industrial_schema_version(version INTEGER PRIMARY KEY,applied_at INTEGER NOT NULL)")
            for migration in sorted(MIGRATIONS_DIRECTORY.glob("00[12]_*.sql")):
                version=int(migration.name[:3]);c.executescript("BEGIN IMMEDIATE;\n"+migration.read_text(encoding="utf-8")+f"\nINSERT INTO industrial_schema_version VALUES({version},unixepoch());\nCOMMIT;")
            c.executescript("""
              INSERT INTO industrial_users(discord_user_id,credits,primary_job) VALUES(1,1234,'miner'),(2,500,'merchant');
              INSERT INTO industrial_companies(id,owner_discord_user_id,name,job_type) VALUES(1,1,'Mine Test','miner'),(2,2,'Trade Test','merchant');
              INSERT INTO industrial_actors(id,actor_type,discord_user_id) VALUES(1,'player',1),(2,'player',2);
              INSERT INTO industrial_inventory(actor_id,owner_discord_user_id,resource_type,quantity) VALUES(1,1,'iron_ore',77);
              INSERT INTO industrial_mines(owner_discord_user_id,company_id,stock) VALUES(1,1,42);
              INSERT INTO industrial_market_orders(id,owner_actor_id,owner_discord_user_id,company_id,side,resource_type,original_quantity,remaining_quantity,unit_price,escrow_quantity,request_id) VALUES(1,1,1,1,'sell','iron_ore',5,5,8,5,'order-old');
              INSERT INTO industrial_forge_jobs(id,owner_discord_user_id,company_id,forge_slot,input_quantity,output_quantity,speed_level_at_start,yield_level_at_start,started_at,finishes_at,status,request_id) VALUES(1,1,1,1,2,2,1,1,10,20,'collected','forge-old');
              INSERT INTO industrial_transports(id,sender_actor_id,receiver_actor_id,operator_actor_id,sender_company_id,receiver_company_id,merchant_discord_user_id,transport_type,resource_type,quantity,departure_at,arrival_at,original_duration_seconds,current_duration_seconds,status,truck_slot,request_id) VALUES(1,1,2,2,1,2,2,'ore_to_blacksmith','iron_ore',3,10,20,10,10,'delivered',1,'transport-old');
              INSERT INTO industrial_bankers(owner_discord_user_id,company_id) VALUES(1,1);
              INSERT INTO industrial_delivery_profiles(discord_user_id,completed_deliveries) VALUES(1,2);
              INSERT INTO industrial_contracts(id,creator_discord_user_id,resource_type,quantity,total_price,escrow_credits,status,request_id) VALUES(1,1,'iron_ore',4,40,40,'open','contract-old');
              INSERT INTO industrial_ai_companies(id,name,job_type,enabled) VALUES(1,'IA Mine','miner',1);
              INSERT INTO industrial_actors(id,actor_type,ai_company_id) VALUES(3,'ai',1);
              INSERT INTO industrial_ai_accounts(actor_id,credits) VALUES(3,900);
              INSERT INTO industrial_ai_production(actor_id,resource_type,total_produced) VALUES(3,'iron_ore',55);
              INSERT INTO industrial_admin_credit_requests(request_id,admin_discord_user_id,target_discord_user_id,operation,amount,balance_before,balance_after) VALUES('admin-old',9,1,'add',100,1134,1234);
            """)
            tables=("industrial_users","industrial_companies","industrial_mines","industrial_inventory","industrial_market_orders","industrial_transports","industrial_forge_jobs","industrial_bankers","industrial_delivery_profiles","industrial_contracts","industrial_ai_companies","industrial_ai_accounts","industrial_ai_production","industrial_admin_credit_requests")
            columns={t:[r[1] for r in c.execute(f"PRAGMA table_info({t})")] for t in tables}
            before={t:[tuple(r) for r in c.execute(f"SELECT {','.join(columns[t])} FROM {t} ORDER BY 1")] for t in tables};c.close();initialize_database_sync(path)
            with closing(connect_database(path)) as c:self.assertEqual(before,{t:[tuple(r) for r in c.execute(f"SELECT {','.join(columns[t])} FROM {t} ORDER BY 1")] for t in tables})


class RegistryAndEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def test_bilan_names_replace_stats_without_removing_aliases(self):
        router=build_economy_router(SQLiteIndustrialEconomyService(SQLiteIndustrialEconomyRepository(Path(tempfile.mkdtemp())/"x.db")))
        self.assertIn("bilan",router.command_names);self.assertIn("indstats",router.command_names);self.assertNotIn("stats",router.command_names);self.assertTrue({"fiche","cv","next","go","progress"}<=router.command_names)

    async def test_new_names_do_not_collide_with_static_legacy_commands(self):
        tree=ast.parse(Path("main.py").read_text(encoding="utf-8"));legacy=set()
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr in {"command","hybrid_command"}:
                        name=node.name;aliases=[]
                        for kw in decorator.keywords:
                            if kw.arg=="name" and isinstance(kw.value,ast.Constant):name=kw.value.value
                            if kw.arg=="aliases" and isinstance(kw.value,(ast.List,ast.Tuple)):aliases=[x.value for x in kw.value.elts if isinstance(x,ast.Constant)]
                        legacy.add(name.casefold());legacy.update(x.casefold() for x in aliases)
        names={"fiche","cv","rank","achievements","succes","objectives","objectifs","bilan","indstats","partners","orders","notifications","adminlog","economycheck"}
        self.assertFalse(names & legacy);self.assertIn("stats",legacy)

    def test_next_still_limits_six(self):
        from tests.test_industrial_next_actions import base_snapshot
        snapshot=base_snapshot(company={"name":"x"},job="miner",wallet=999999,mine={"stock":50,"capacity":100,"storage_level":1,"production_level":1,"quality_level":1,"seconds_to_full":60},inventory={"iron_ore":50},contracts={"iron_ore":2},best_iron_ore_buy_price=10,available_delivery_missions=1,open_market_orders=2,partner_count=1,nearest_objective={"progress":19,"target":20})
        self.assertLessEqual(len(build_recommendations(snapshot)),6)

    async def test_embed_limits_and_admin_refusal(self):
        class Channel:
            def __init__(self):self.sent=[]
            async def send(self,content=None,**kwargs):self.sent.append((content,kwargs))
        class Service:
            async def get_industrial_profile(self,uid):return {"company":{"name":"Entreprise"},"inventory":{"iron_ore":1,"iron_ingot":2},"job":"miner","company_size":"Petite entreprise","credits":10,"company_value":20,"reputation":3,"money_rank":1,"ore_produced":4,"ingots_forged":5,"market_volume":6,"transports":7,"deliveries":8,"contracts_completed":9,"achievement_titles":["Titre"]}
            async def economy_check(self):raise AssertionError("non-admin must not access SQLite")
        channel=Channel();message=SimpleNamespace(author=SimpleNamespace(id=1,guild_permissions=SimpleNamespace(administrator=False)),channel=channel)
        await build_fiche_command(Service())(EconomyCommandContext(message,(),None));embed=channel.sent[0][1]["embed"]
        self.assertLessEqual(len(embed.fields),25);self.assertTrue(all(len(f.value)<=1024 for f in embed.fields));self.assertLessEqual(len(embed),6000)
        channel.sent.clear();await build_economycheck_command(Service())(EconomyCommandContext(message,(),None));self.assertIn("permission",channel.sent[0][0])

    async def test_backup_scheduler_is_disabled_and_singleton(self):
        import os
        old=os.environ.get("INDUSTRIAL_BACKUP_ENABLED")
        try:
            os.environ["INDUSTRIAL_BACKUP_ENABLED"]="false";self.assertIsNone(backups.start_backup_scheduler())
            os.environ["INDUSTRIAL_BACKUP_ENABLED"]="true"
            first=backups.start_backup_scheduler();second=backups.start_backup_scheduler();self.assertIs(first,second)
            await backups.stop_backup_scheduler();self.assertIsNone(backups._task)
        finally:
            if old is None:os.environ.pop("INDUSTRIAL_BACKUP_ENABLED",None)
            else:os.environ["INDUSTRIAL_BACKUP_ENABLED"]=old
