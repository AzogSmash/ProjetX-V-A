import tempfile
import time
import unittest
import sqlite3
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from economy_v2.database import MIGRATIONS_DIRECTORY, connect_database, immediate_transaction, initialize_database_sync
from economy_v2.forge_config import get_forge_duration_seconds
from economy_v2.merchant_config import get_trip_duration_seconds
from economy_v2.mining_config import get_production_rate
from economy_v2.sqlite_repository import SQLiteIndustrialEconomyRepository
from economy_v2.systems_repository import EVENT_CYCLE_SECONDS,EVENT_DEFINITIONS,EVENT_EXTENSION_THRESHOLD_SECONDS,EVENT_HORIZON_SECONDS
from economy_v2.commands.systems import build_events_command,build_season_command,build_team_command,build_titles_command
from economy_v2.commands.next_actions import build_recommendations
from economy_v2.router import EconomyCommandContext


class SystemsCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/"economy.db";self.repo=SQLiteIndustrialEconomyRepository(self.path)
    def tearDown(self):self.tmp.cleanup()
    def company(self,uid,name,job):return self.repo.create_first_company(uid,name,job)[1]
    def event(self,kind,bps):
        now=int(time.time())
        with immediate_transaction(self.path) as c:
            c.execute("DELETE FROM industrial_economic_events")
            c.execute("INSERT INTO industrial_economic_events(cycle_key,event_type,display_name,starts_at,ends_at,multiplier_bps,status) VALUES(?,?,?,?,?,?,'active')",(f"test:{kind}",kind,kind,now-10,now+3600,bps))


class SeasonTests(SystemsCase):
    def test_creation_score_and_ranking_use_real_actions(self):
        self.company(1,"Mine One","miner");self.company(2,"Mine Two","miner")
        first=self.repo.get_season_dashboard(1);season=first["season"]
        with immediate_transaction(self.path) as c:
            a1=self.repo._actor_id(c,1);a2=self.repo._actor_id(c,2)
            c.execute("INSERT INTO industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity,created_at) VALUES(?,'player','mine_production','iron_ore',50,?)",(a1,season["starts_at"]+1))
            c.execute("INSERT INTO industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity,created_at) VALUES(?,'player','mine_production','iron_ore',20,?)",(a2,season["starts_at"]+1))
        self.repo.get_season_dashboard(2,"mine");result=self.repo.get_season_dashboard(1,"mine")
        self.assertEqual(50,result["scores"]["mine"]);self.assertEqual(1,result["rank"]);self.assertEqual(1,result["ranking"][0]["discord_user_id"])

    def test_rollover_is_concurrent_idempotent_and_never_resets_economy(self):
        self.company(1,"Mine One","miner");self.repo.adjust_admin_credits(9,1,"add",4321,"cash");old=self.repo.get_season_dashboard(1)["season"]
        with immediate_transaction(self.path) as c:
            aid=self.repo._actor_id(c,1);c.execute("INSERT INTO industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity,created_at) VALUES(?,'player','mine_production','iron_ore',100,?)",(aid,old["starts_at"]+1));c.execute("UPDATE industrial_seasons SET ends_at=? WHERE id=?",(int(time.time())-1,old["id"]))
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:self.repo.get_season_dashboard(1),range(2)))
        with closing(connect_database(self.path)) as c:
            self.assertEqual(1,c.execute("SELECT count(*) FROM industrial_seasons WHERE status='finished'").fetchone()[0]);self.assertEqual(1,c.execute("SELECT count(*) FROM industrial_seasons WHERE status='active'").fetchone()[0]);self.assertEqual(1,c.execute("SELECT count(*) FROM industrial_season_rewards WHERE season_id=? AND category='mine'",(old["id"],)).fetchone()[0]);self.assertEqual(4321,c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=1").fetchone()[0])

    def test_rewards_and_titles_are_unique(self):
        self.test_rollover_is_concurrent_idempotent_and_never_resets_economy()
        with closing(connect_database(self.path)) as c:
            rewards=c.execute("SELECT count(*),count(DISTINCT category||':'||reward_type) FROM industrial_season_rewards").fetchone();self.assertEqual(rewards[0],rewards[1]);self.assertEqual(0,c.execute("SELECT count(*) FROM industrial_transactions WHERE transaction_type='season_reward'").fetchone()[0])


class TitleTests(SystemsCase):
    def test_unlock_equip_remove_idempotently_without_money(self):
        self.repo.adjust_admin_credits(9,1,"add",1_000_000,"seed");before=self.repo.get_or_create_user(1).credits
        first=self.repo.refresh_titles(1);second=self.repo.refresh_titles(1);self.assertEqual(first,second)
        title=self.repo.equip_title(1,"magnate","e1");self.repo.equip_title(1,"magnate","e1")
        with immediate_transaction(self.path) as c:
            tid=c.execute("SELECT id FROM industrial_titles WHERE slug='master_miner'").fetchone()[0];c.execute("INSERT INTO industrial_user_titles(discord_user_id,title_id,source_reference) VALUES(1,?,'test')",(tid,))
        with self.assertRaises(ValueError):self.repo.equip_title(1,"master_miner","e1")
        with closing(connect_database(self.path)) as c:self.assertEqual(1,c.execute("SELECT count(*) FROM industrial_user_titles WHERE discord_user_id=1 AND equipped=1").fetchone()[0])
        self.repo.remove_title(1,"remove")
        with closing(connect_database(self.path)) as c:self.assertEqual(0,c.execute("SELECT count(*) FROM industrial_user_titles WHERE discord_user_id=1 AND equipped=1").fetchone()[0])
        self.assertEqual(before,self.repo.get_or_create_user(1).credits);self.assertEqual("magnate",title["slug"])
        profile=self.repo.get_industrial_profile(1);self.assertIsNone(profile["equipped_title"])


class EventTests(SystemsCase):
    def test_events_are_persistent_bounded_and_expire(self):
        self.repo.ensure_current_event()
        rows=self.repo.get_active_events();self.assertEqual(1,len(rows));self.assertTrue(8000<=rows[0]["multiplier_bps"]<=12500)
        restarted=SQLiteIndustrialEconomyRepository(self.path);self.assertEqual(rows,restarted.get_active_events())
        with self.assertRaises(Exception):
            with immediate_transaction(self.path) as c:c.execute("INSERT INTO industrial_economic_events(cycle_key,event_type,display_name,starts_at,ends_at,multiplier_bps) VALUES('bad','mining_rush','bad',1,2,50000)")
        after=self.repo.get_active_events(rows[0]["ends_at"]+1);self.assertTrue(all(r["starts_at"]>=rows[0]["ends_at"] for r in after))

    def test_mining_and_forge_multipliers_apply_once(self):
        self.company(1,"Mine One","miner");self.event("mining_rush",11500)
        now=int(time.time())
        with immediate_transaction(self.path) as c:c.execute("INSERT INTO industrial_mines(owner_discord_user_id,company_id,last_production_at) VALUES(1,1,?)",(now-3600,))
        mine=self.repo.get_or_create_and_refresh_mine(1)[2];self.assertEqual(get_production_rate(1)*115//100,mine.stock)
        self.company(2,"Forge Two","blacksmith");self.event("industrial_boom",11500)
        with immediate_transaction(self.path) as c:self.repo._add_inventory(c,2,"iron_ore",10)
        job=self.repo.start_forge_job(2,"iron_ore",10,"forge-event")[3].job
        self.assertEqual(get_forge_duration_seconds(10,1)*10000//11500,int(job.finishes_at)-int(job.started_at))

    def test_transport_world_price_and_delivery_xp_multipliers(self):
        merchant=self.company(1,"Trade One","merchant");receiver=self.company(2,"Forge Two","blacksmith");self.repo.adjust_admin_credits(9,1,"add",10000,"m-cash");self.event("logistics_rush",8500)
        with immediate_transaction(self.path) as c:self.repo._add_inventory(c,1,"iron_ore",10)
        transport=self.repo.start_transport(1,2,"iron_ore",5,"trip")[3].transport;self.assertEqual(get_trip_duration_seconds(1)*8500//10000,int(transport.arrival_at)-int(transport.departure_at))
        self.company(3,"Bank Three","banker");self.event("world_demand",11000)
        with immediate_transaction(self.path) as c:self.repo._add_inventory(c,3,"iron_ingot",2)
        sale=self.repo.sell_world_ingots(3,1,"sale")[3];self.assertEqual(88,sale.unit_price)
        self.event("delivery_bonus",11000)
        with immediate_transaction(self.path) as c:
            c.execute("UPDATE industrial_transports SET arrival_at=?,current_duration_seconds=3600,status='in_transit' WHERE id=?",(int(time.time())+3600,transport.id));c.execute("UPDATE industrial_delivery_missions SET status='open' WHERE transport_id=?",(transport.id,))
        result=self.repo.accept_delivery(4,transport.id,"delivery");self.assertGreater(result.get("xp_awarded",0),0)
        before=self.repo.get_delivery_profile(4).delivery_xp;duplicate=self.repo.accept_delivery(4,transport.id,"delivery");after=self.repo.get_delivery_profile(4).delivery_xp
        self.assertEqual("duplicate",duplicate["result_status"]);self.assertEqual(before,after)

    def test_nearly_exhausted_horizon_extends_lazily_beyond_one_year(self):
        origin=1_800_000_000;self.repo.ensure_current_event(origin)
        with closing(connect_database(self.path)) as c:old_max=int(c.execute("SELECT max(ends_at) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()[0])
        near_end=old_max-EVENT_EXTENSION_THRESHOLD_SECONDS+1
        self.repo.get_active_events(near_end)
        with closing(connect_database(self.path)) as c:
            new_max=int(c.execute("SELECT max(ends_at) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()[0])
            self.assertGreater(new_max,old_max);self.assertGreaterEqual(new_max,near_end+EVENT_HORIZON_SECONDS)
        beyond_year=origin+EVENT_HORIZON_SECONDS+7*86400
        self.assertEqual(1,len(self.repo.get_active_events(beyond_year)))

    def test_concurrent_horizon_extension_is_idempotent_without_duplicates(self):
        origin=1_800_000_000;self.repo.ensure_current_event(origin)
        with closing(connect_database(self.path)) as c:max_end=int(c.execute("SELECT max(ends_at) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()[0])
        near_end=max_end-EVENT_EXTENSION_THRESHOLD_SECONDS+1
        repositories=(SQLiteIndustrialEconomyRepository(self.path),SQLiteIndustrialEconomyRepository(self.path))
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda repo:repo.get_active_events(near_end),repositories))
        with closing(connect_database(self.path)) as c:
            count,distinct_count=c.execute("SELECT count(*),count(DISTINCT cycle_key) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()
            self.assertEqual(count,distinct_count)
            extended_count=count
        self.repo.get_active_events(near_end)
        with closing(connect_database(self.path)) as c:self.assertEqual(extended_count,c.execute("SELECT count(*) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()[0])

    def test_extension_preserves_history_and_is_deterministic(self):
        origin=1_800_000_000;self.repo.ensure_current_event(origin)
        with immediate_transaction(self.path) as c:
            c.execute("INSERT INTO industrial_economic_events(cycle_key,event_type,display_name,starts_at,ends_at,multiplier_bps,status,created_at) VALUES('historical:test','mining_rush','Historique',10,20,11500,'finished',30)")
            historical=tuple(c.execute("SELECT * FROM industrial_economic_events WHERE cycle_key='historical:test'").fetchone())
            old_max=int(c.execute("SELECT max(ends_at) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()[0])
        near_end=old_max-EVENT_EXTENSION_THRESHOLD_SECONDS+1;self.repo.get_active_events(near_end)
        with closing(connect_database(self.path)) as c:
            self.assertEqual(historical,tuple(c.execute("SELECT * FROM industrial_economic_events WHERE cycle_key='historical:test'").fetchone()))
            rows=c.execute("SELECT cycle_key,event_type,display_name,multiplier_bps,starts_at,ends_at FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%' AND starts_at>=? ORDER BY starts_at",(old_max,)).fetchall()
            self.assertTrue(rows)
            for row in rows:
                cycle=int(row[0].split(":",1)[1]);expected=EVENT_DEFINITIONS[cycle%len(EVENT_DEFINITIONS)]
                self.assertEqual((expected[0],expected[1],expected[2],cycle*EVENT_CYCLE_SECONDS,(cycle+1)*EVENT_CYCLE_SECONDS),tuple(row[1:]))

    def test_extended_horizon_survives_restart_without_replay(self):
        origin=1_800_000_000;self.repo.ensure_current_event(origin)
        with closing(connect_database(self.path)) as c:old_max=int(c.execute("SELECT max(ends_at) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()[0])
        near_end=old_max-EVENT_EXTENSION_THRESHOLD_SECONDS+1;self.repo.get_active_events(near_end)
        with closing(connect_database(self.path)) as c:before=c.execute("SELECT count(*),max(ends_at) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()
        restarted=SQLiteIndustrialEconomyRepository(self.path);self.assertEqual(1,len(restarted.get_active_events(near_end)))
        with closing(connect_database(self.path)) as c:self.assertEqual(tuple(before),tuple(c.execute("SELECT count(*),max(ends_at) FROM industrial_economic_events WHERE cycle_key LIKE 'cycle:%'").fetchone()))


class TeamTests(SystemsCase):
    def setUp(self):super().setUp();self.company(1,"Team One","miner");self.repo.get_or_create_user(2);self.repo.get_or_create_user(3)
    def test_invite_accept_permissions_roles_remove_and_audit(self):
        invite=self.repo.invite_team_member(1,2,"invite");duplicate=self.repo.invite_team_member(1,2,"invite");self.assertEqual(invite["id"],duplicate["id"]);self.assertEqual("duplicate",duplicate["status"]);self.assertEqual("accepted",self.repo.resolve_team_invitation(2,invite["id"],"accept","accept")["status"])
        with self.assertRaises(ValueError):self.repo.invite_team_member(1,3,"invite")
        self.assertEqual("forbidden",self.repo.invite_team_member(2,3,"employee-invite")["status"])
        self.assertEqual("manager",self.repo.change_team(1,"role",2,"manager","promote")["status"]);self.assertEqual("pending",self.repo.invite_team_member(2,3,"manager-invite")["status"])
        self.assertEqual("removed",self.repo.change_team(1,"remove",2,None,"remove")["status"]);self.assertEqual("forbidden",self.repo.invite_team_member(2,3,"revoked")["status"])
        with closing(connect_database(self.path)) as c:self.assertGreaterEqual(c.execute("SELECT count(*) FROM industrial_team_audit").fetchone()[0],4)
        self.assertTrue(any(row["transaction_type"].startswith("team_") for row in self.repo.get_admin_log(1)))
        with closing(connect_database(self.path)) as c:self.assertEqual(1,c.execute("SELECT count(*) FROM industrial_notification_events WHERE discord_user_id=2 AND event_type='team'").fetchone()[0])

    def test_decline_expiration_leave_and_owner_guards(self):
        invite=self.repo.invite_team_member(1,2,"i1");self.assertEqual("declined",self.repo.resolve_team_invitation(2,invite["id"],"decline","d1")["status"])
        expired=self.repo.invite_team_member(1,3,"i2")
        with immediate_transaction(self.path) as c:c.execute("UPDATE industrial_team_invitations SET expires_at=? WHERE id=?",(int(time.time())-1,expired["id"]))
        self.assertEqual("expired",self.repo.resolve_team_invitation(3,expired["id"],"accept","a2")["status"]);self.assertEqual("owner_cannot_leave",self.repo.change_team(1,"leave",None,None,"leave-owner")["status"])

    def test_no_team_action_changes_wallet_or_personal_job(self):
        before=self.repo.get_or_create_user(1);invite=self.repo.invite_team_member(1,2,"i");self.repo.resolve_team_invitation(2,invite["id"],"accept","a")
        after=self.repo.get_or_create_user(1);employee=self.repo.get_or_create_user(2);self.assertEqual((before.credits,before.primary_job),(after.credits,after.primary_job));self.assertIsNone(employee.primary_job)
        self.assertEqual("employee",self.repo.get_industrial_profile(2)["team_roles"][0]["role"])

    def test_next_recommends_pending_invitation_read_only(self):
        invite=self.repo.invite_team_member(1,2,"next-invite");snapshot=self.repo.get_next_actions_snapshot(2);before=self.repo.get_team(2)
        from tests.test_industrial_next_actions import base_snapshot
        recommendations=build_recommendations(base_snapshot(team_invitations=snapshot["team_invitations"]));self.assertIn("?equipe",[r[3] for r in recommendations]);self.assertEqual(before,self.repo.get_team(2));self.assertEqual("pending",invite["status"])


class MigrationSixTests(unittest.TestCase):
    def test_version_005_upgrade_preserves_rows_restart_and_backup_schema(self):
        from economy_v2.backups import backup_once
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/"v5.db";c=connect_database(path);c.execute("CREATE TABLE industrial_schema_version(version INTEGER PRIMARY KEY,applied_at INTEGER NOT NULL)")
            for migration in sorted(MIGRATIONS_DIRECTORY.glob("00[1-5]_*.sql")):
                version=int(migration.name[:3]);c.executescript("BEGIN IMMEDIATE;\n"+migration.read_text(encoding="utf-8")+f"\nINSERT INTO industrial_schema_version VALUES({version},unixepoch());\nCOMMIT;")
            c.execute("INSERT INTO industrial_users(discord_user_id,credits) VALUES(7,777)");c.close();initialize_database_sync(path);initialize_database_sync(path)
            with closing(connect_database(path)) as c:self.assertEqual(777,c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=7").fetchone()[0]);self.assertEqual([1,2,3,4,5,6,7],[r[0] for r in c.execute("SELECT version FROM industrial_schema_version ORDER BY version")]);self.assertEqual("ok",c.execute("PRAGMA integrity_check").fetchone()[0]);self.assertEqual([],c.execute("PRAGMA foreign_key_check").fetchall())
            backup=backup_once(path,root/"backups",2,123);self.assertTrue(backup.exists())
            with closing(sqlite3.connect(backup)) as c:self.assertEqual(1,c.execute("SELECT count(*) FROM sqlite_master WHERE name='industrial_seasons'").fetchone()[0])


class CommandEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_new_embeds_respect_discord_limits(self):
        class Channel:
            def __init__(self):self.sent=[]
            async def send(self,content=None,**kwargs):self.sent.append((content,kwargs))
        class Service:
            async def get_season_dashboard(self,uid,category):return {"season":{"name":"Saison 1","starts_at":1,"ends_at":int(time.time())+1000},"scores":{category:10},"rank":1,"ranking":[{"discord_user_id":uid,"score":10}],"category":category}
            async def refresh_titles(self,uid):return [{"slug":"title","display_name":"Titre","rarity":"rare","equipped":1}]
            async def get_active_events(self):return [{"event_type":"mining_rush","display_name":"Ruée","ends_at":int(time.time())+1000,"multiplier_bps":11500}]
            async def get_team(self,uid):return {"teams":[{"name":"Entreprise","role":"owner","members":[{"discord_user_id":uid,"role":"owner"}]}],"invitations":[]}
        channel=Channel();message=SimpleNamespace(author=SimpleNamespace(id=1),channel=channel,id=9)
        for builder in (build_season_command,build_titles_command,build_events_command,build_team_command):
            channel.sent.clear();await builder(Service())(EconomyCommandContext(message,(),None));embed=channel.sent[0][1]["embed"]
            self.assertLessEqual(len(embed.fields),25);self.assertTrue(all(len(field.value)<=1024 for field in embed.fields));self.assertLessEqual(len(embed),6000)
