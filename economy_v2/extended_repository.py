from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from economy_v2.database import immediate_transaction
from economy_v2.progression import ACHIEVEMENTS, COMMON_OBJECTIVES, OBJECTIVE_DEFINITIONS, activity_reputation, company_size, company_value


def _now(): return int(time.time())


def _period_start(period, now):
    current = datetime.fromtimestamp(now, timezone.utc)
    start = int(datetime(current.year, current.month, current.day, tzinfo=timezone.utc).timestamp())
    return start if period == "daily" else start - current.weekday() * 86400


class IndustrialInsightsMixin:
    """Analytics and non-monetary progression for the industrial SQLite store."""

    def _metrics(self, c, uid, since=None):
        user = c.execute("SELECT credits,primary_job FROM industrial_users WHERE discord_user_id=?", (uid,)).fetchone()
        if not user: return None
        company = c.execute("SELECT id,name,job_type,level FROM industrial_companies WHERE owner_discord_user_id=?", (uid,)).fetchone()
        actor = c.execute("SELECT id FROM industrial_actors WHERE discord_user_id=?", (uid,)).fetchone()
        aid = int(actor[0]) if actor else -1
        inv = {r[0]: int(r[1]) for r in c.execute("SELECT resource_type,quantity FROM industrial_inventory WHERE owner_discord_user_id=?", (uid,))}
        tail, ap = ("", (aid,)) if since is None else (" AND created_at>=?", (aid, since))
        def resource(resource, event):
            return int(c.execute("SELECT coalesce(sum(quantity),0) FROM industrial_resource_events WHERE actor_id=? AND resource_type=? AND event_type=?" + tail, (aid, resource, event) + (() if since is None else (since,))).fetchone()[0])
        ore, ingots = resource("iron_ore", "mine_production"), resource("iron_ingot", "forge_output")
        tt, tp = ("", (uid, uid)) if since is None else (" AND created_at>=?", (uid, uid, since))
        trade = c.execute("SELECT coalesce(sum(total_price),0),coalesce(sum(quantity),0),count(*) FROM industrial_market_trades WHERE (seller_discord_user_id=? OR buyer_discord_user_id=?)" + tt, tp).fetchone()
        def trades(column):
            p = (uid,) if since is None else (uid, since)
            return int(c.execute(f"SELECT coalesce(sum(quantity),0) FROM industrial_market_trades WHERE {column}=?" + tt, p).fetchone()[0])
        transport = c.execute("SELECT coalesce(sum(quantity),0),count(*) FROM industrial_transports WHERE operator_actor_id=?" + tail, ap).fetchone()
        dt = "" if since is None else " AND accepted_at>=?"; dp = (uid,) if since is None else (uid, since)
        deliveries = int(c.execute("SELECT count(*) FROM industrial_delivery_missions WHERE courier_discord_user_id=? AND status='accepted'" + dt, dp).fetchone()[0])
        ct = "" if since is None else " AND completed_at>=?"; cp = (uid,) if since is None else (uid, since)
        contracts = int(c.execute("SELECT count(*) FROM industrial_contracts WHERE accepter_discord_user_id=? AND status='completed'" + ct, cp).fetchone()[0])
        wt = "" if since is None else " AND created_at>=?"; wp = (uid,) if since is None else (uid, since)
        world = c.execute("SELECT coalesce(sum(quantity),0),coalesce(sum(total_credits),0) FROM industrial_world_sales WHERE banker_discord_user_id=?" + wt, wp).fetchone()
        infra = {}
        for key, table, cols in (("mine","industrial_mines","storage_level,production_level,quality_level"),("merchant","industrial_merchants","truck_count,truck_capacity_level,truck_speed_level,warehouse_level"),("forge","industrial_blacksmiths","forge_level,speed_level,storage_level,yield_level")):
            row = c.execute(f"SELECT {cols} FROM {table} WHERE owner_discord_user_id=?", (uid,)).fetchone()
            if row: infra[key] = dict(row)
        rep = int(c.execute("SELECT coalesce(sum(reputation),0) FROM industrial_reputation_events WHERE discord_user_id=?", (uid,)).fetchone()[0])
        result = {"credits":int(user[0]),"inventory":inv,"infrastructure":infra,"ore_produced":ore,"ingots_forged":ingots,"resources_produced":ore+ingots,"market_volume":int(trade[0]),"market_units":int(trade[1]),"market_trades":int(trade[2]),"ore_sold":trades("seller_discord_user_id"),"ore_bought":trades("buyer_discord_user_id"),"transport_volume":int(transport[0]),"transports":int(transport[1]),"deliveries":deliveries,"contracts_completed":contracts,"ingots_sold":int(world[0]),"world_sales_credits":int(world[1])}
        value = company_value(result)
        result.update({"discord_user_id":uid,"job":user[1],"company":dict(company) if company else None,"company_value":value,"company_size":company_size(value),"reputation":activity_reputation(result)+rep,"achievements":int(c.execute("SELECT count(*) FROM industrial_achievements WHERE discord_user_id=?",(uid,)).fetchone()[0])})
        return result

    def get_industrial_profile(self, uid):
        with self._read() as c:
            result = self._metrics(c, uid)
            if not result: return None
            result["achievement_titles"] = [r[0] for r in c.execute("SELECT title FROM industrial_achievements WHERE discord_user_id=? ORDER BY earned_at DESC LIMIT 5", (uid,))]
            result["money_rank"] = int(c.execute("SELECT count(*)+1 FROM industrial_users WHERE credits>?", (result["credits"],)).fetchone()[0])
            row = c.execute("SELECT last_active_at,command_count FROM industrial_user_activity WHERE discord_user_id=?", (uid,)).fetchone(); result["activity"] = dict(row) if row else None
            return result

    def get_rankings(self, category, limit=10):
        if category not in {"money","companies","production","market","delivery","contracts"}: raise ValueError("invalid ranking category")
        with self._read() as c: rows = [self._metrics(c, int(r[0])) for r in c.execute("SELECT discord_user_id FROM industrial_users")]
        keys={"money":"credits","companies":"company_value","market":"market_volume","delivery":"deliveries","contracts":"contracts_completed"}
        score=(lambda r:r["ore_produced"]+r["ingots_forged"]) if category=="production" else (lambda r:r[keys[category]])
        return sorted(rows,key=lambda r:(-score(r),r["discord_user_id"]))[:min(10,max(1,limit))]

    def refresh_achievements(self, uid):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,uid); metrics=self._metrics(c,uid)
            for item in ACHIEVEMENTS:
                if int(metrics.get(item.metric,0))>=item.threshold:
                    fresh=c.execute("INSERT OR IGNORE INTO industrial_achievements(discord_user_id,achievement_key,title,reputation_awarded) VALUES(?,?,?,?)",(uid,item.key,item.title,item.reputation)).rowcount
                    if fresh:c.execute("INSERT OR IGNORE INTO industrial_reputation_events(discord_user_id,event_type,reputation,source_key) VALUES(?,?,?,?)",(uid,"achievement",item.reputation,f"achievement:{item.key}"))
            return [dict(r) for r in c.execute("SELECT achievement_key,title,reputation_awarded,earned_at FROM industrial_achievements WHERE discord_user_id=? ORDER BY earned_at,id",(uid,))]

    def get_objectives(self, uid, now=None):
        now=int(now or _now())
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,uid);job=c.execute("SELECT primary_job FROM industrial_users WHERE discord_user_id=?",(uid,)).fetchone()[0];out=[]
            for period,mult,award in (("daily",1,3),("weekly",5,10)):
                start=_period_start(period,now);metrics=self._metrics(c,uid,start)
                for key,label,metric in OBJECTIVE_DEFINITIONS.get(job,())+COMMON_OBJECTIVES:
                    target=(1 if metric in {"deliveries","contracts_completed"} else 20)*mult; progress=int(metrics.get(metric,0));done=progress>=target
                    c.execute("INSERT INTO industrial_objective_progress(discord_user_id,objective_key,period_type,period_start,target,progress,completed_at,reputation_awarded,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(discord_user_id,objective_key,period_type,period_start) DO UPDATE SET progress=excluded.progress,completed_at=coalesce(industrial_objective_progress.completed_at,excluded.completed_at),updated_at=excluded.updated_at",(uid,key,period,start,target,progress,now if done else None,award if done else 0,now))
                    if done:c.execute("INSERT OR IGNORE INTO industrial_reputation_events(discord_user_id,event_type,reputation,source_key) VALUES(?,?,?,?)",(uid,"objective",award,f"objective:{period}:{start}:{key}"))
                    out.append({"key":key,"label":label,"period":period,"period_start":start,"target":target,"progress":progress,"completed":done})
            return out

    def get_player_stats(self,uid):
        with self._read() as c:
            result=self._metrics(c,uid)
            if not result:return None
            result["last_7_days"]=self._metrics(c,uid,_now()-604800);result["last_30_days"]=self._metrics(c,uid,_now()-2592000)
            actor=c.execute("SELECT id FROM industrial_actors WHERE discord_user_id=?",(uid,)).fetchone();aid=int(actor[0]) if actor else -1
            cash=c.execute("SELECT coalesce(sum(CASE WHEN monetary_effect='source' THEN credits ELSE 0 END),0),coalesce(sum(CASE WHEN monetary_effect='sink' THEN credits ELSE 0 END),0) FROM industrial_transactions WHERE actor_id=?",(aid,)).fetchone();result["credits_gained"],result["credits_spent"]=map(int,cash);return result

    def get_orders_overview(self,uid):
        with self._read() as c:
            actor=c.execute("SELECT id FROM industrial_actors WHERE discord_user_id=?",(uid,)).fetchone();aid=int(actor[0]) if actor else -1;now=_now()
            return {"market_orders":[dict(r) for r in c.execute("SELECT id,side,resource_type,remaining_quantity,unit_price,status FROM industrial_market_orders WHERE owner_discord_user_id=? AND status='open' ORDER BY created_at DESC LIMIT 10",(uid,))],"contracts":[dict(r) for r in c.execute("SELECT id,resource_type,quantity,total_price,status,expires_at FROM industrial_contracts WHERE (creator_discord_user_id=? OR accepter_discord_user_id=?) AND status='open' AND expires_at>? LIMIT 10",(uid,uid,now))],"transports":[dict(r) for r in c.execute("SELECT id,resource_type,quantity,status,arrival_at FROM industrial_transports WHERE (operator_actor_id=? OR sender_actor_id=? OR receiver_actor_id=?) AND status='in_transit' ORDER BY arrival_at LIMIT 10",(aid,aid,aid))],"forge_jobs":[dict(r) for r in c.execute("SELECT id,input_quantity,output_quantity,status,finishes_at FROM industrial_forge_jobs WHERE owner_discord_user_id=? AND status IN('processing','completed') LIMIT 10",(uid,))],"shipments":[dict(r) for r in c.execute("SELECT id,quantity,status FROM industrial_ingot_shipments WHERE (blacksmith_discord_user_id=? OR merchant_discord_user_id=? OR banker_discord_user_id=?) AND status='pending' LIMIT 10",(uid,uid,uid))],"missions":[dict(r) for r in c.execute("SELECT id,status,commission_max FROM industrial_delivery_missions WHERE courier_discord_user_id=? AND status='accepted' LIMIT 10",(uid,))],"cooldown_until":int((c.execute("SELECT delivery_cooldown_until FROM industrial_delivery_profiles WHERE discord_user_id=?",(uid,)).fetchone() or [0])[0] or 0)}

    def update_partnership(self,uid,target,action,request_id):
        if uid==target or action not in {"add","remove"}:raise ValueError("invalid partnership")
        low,high=sorted((uid,target));compatible=({"miner","merchant"},{"merchant","blacksmith"},{"blacksmith","banker"})
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,uid);users=c.execute("SELECT discord_user_id,primary_job FROM industrial_users WHERE discord_user_id IN (?,?)",(uid,target)).fetchall()
            if len(users)!=2:return {"status":"unknown_target"}
            if {r[1] for r in users} not in compatible:return {"status":"incompatible"}
            row=c.execute("SELECT * FROM industrial_partnerships WHERE low_user_id=? AND high_user_id=?",(low,high)).fetchone()
            if action=="remove":
                if not row:return {"status":"not_found"}
                c.execute("UPDATE industrial_partnerships SET status='removed',removed_at=?,updated_at=? WHERE id=?",(_now(),_now(),row["id"]));return {"status":"removed"}
            if row and row["status"]=="accepted":return {"status":"accepted"}
            if row and row["status"]=="pending" and int(row["target_discord_user_id"])==uid:c.execute("UPDATE industrial_partnerships SET status='accepted',accepted_at=?,updated_at=? WHERE id=?",(_now(),_now(),row["id"]));return {"status":"accepted"}
            if row:c.execute("UPDATE industrial_partnerships SET requester_discord_user_id=?,target_discord_user_id=?,status='pending',request_id=?,accepted_at=NULL,removed_at=NULL,updated_at=? WHERE id=?",(uid,target,request_id,_now(),row["id"]))
            else:c.execute("INSERT INTO industrial_partnerships(requester_discord_user_id,target_discord_user_id,low_user_id,high_user_id,request_id) VALUES(?,?,?,?,?)",(uid,target,low,high,request_id))
            return {"status":"pending"}

    def get_partnerships(self,uid):
        with self._read() as c:return [dict(r) for r in c.execute("SELECT * FROM industrial_partnerships WHERE (low_user_id=? OR high_user_id=?) AND status IN('pending','accepted') ORDER BY updated_at DESC",(uid,uid))]

    def get_notification_preferences(self,uid):
        with self._read() as c:
            row=c.execute("SELECT * FROM industrial_notification_preferences WHERE discord_user_id=?",(uid,)).fetchone();return dict(row) if row else {"enabled":1,"market_enabled":1,"transport_enabled":1,"forge_enabled":1,"shipment_enabled":1,"contract_enabled":1}

    def set_notification_preference(self,uid,category,enabled):
        columns={"all":"enabled","market":"market_enabled","transport":"transport_enabled","forge":"forge_enabled","shipment":"shipment_enabled","contract":"contract_enabled"}
        if category not in columns:raise ValueError("invalid category")
        with immediate_transaction(self.database_path) as c:self._ensure_user(c,uid);c.execute("INSERT OR IGNORE INTO industrial_notification_preferences(discord_user_id) VALUES(?)",(uid,));c.execute(f"UPDATE industrial_notification_preferences SET {columns[category]}=?,updated_at=? WHERE discord_user_id=?",(int(enabled),_now(),uid));return dict(c.execute("SELECT * FROM industrial_notification_preferences WHERE discord_user_id=?",(uid,)).fetchone())

    def enqueue_notification(self,uid,event_type,event_key,payload=None):
        with immediate_transaction(self.database_path) as c:self._ensure_user(c,uid);return bool(c.execute("INSERT OR IGNORE INTO industrial_notification_events(discord_user_id,event_type,event_key,payload) VALUES(?,?,?,?)",(uid,event_type,event_key,json.dumps(payload or {}))).rowcount)

    def get_admin_log(self,uid,limit=20):
        with self._read() as c:
            actor=c.execute("SELECT id FROM industrial_actors WHERE discord_user_id=?",(uid,)).fetchone();aid=int(actor[0]) if actor else -1
            return [dict(r) for r in c.execute("SELECT transaction_type,monetary_effect,credits,resource_type,quantity,reference_type,reference_id,metadata,created_at FROM industrial_transactions WHERE actor_id=? OR counterparty_actor_id=? OR json_extract(metadata,'$.target_discord_user_id')=? ORDER BY created_at DESC,id DESC LIMIT ?",(aid,aid,uid,limit))]

    def economy_check(self):
        with self._read() as c:
            integrity=c.execute("PRAGMA integrity_check").fetchone()[0];foreign=c.execute("PRAGMA foreign_key_check").fetchall();queries={"Wallets":"SELECT count(*) FROM industrial_users WHERE credits<0","Inventaires":"SELECT count(*) FROM industrial_inventory WHERE quantity<0","Escrows":"SELECT count(*) FROM industrial_market_orders WHERE escrow_credits<0 OR escrow_quantity<0","Marche":"SELECT count(*) FROM industrial_market_orders WHERE remaining_quantity<0 OR remaining_quantity>original_quantity","Transports":"SELECT count(*) FROM industrial_transports WHERE arrival_at<departure_at OR current_duration_seconds<0","Forge":"SELECT count(*) FROM industrial_forge_jobs WHERE output_quantity<=0 OR finishes_at<started_at","Contrats":"SELECT count(*) FROM industrial_contracts WHERE escrow_credits<0 OR (status='open' AND escrow_credits<>total_price)","Acteurs":"SELECT count(*) FROM industrial_actors a LEFT JOIN industrial_users u ON u.discord_user_id=a.discord_user_id LEFT JOIN industrial_ai_companies ai ON ai.id=a.ai_company_id WHERE (a.actor_type='player' AND u.discord_user_id IS NULL) OR (a.actor_type='ai' AND ai.id IS NULL)","Request IDs":"SELECT count(*) FROM (SELECT request_id,count(*) n FROM industrial_market_orders GROUP BY request_id HAVING n>1)"};checks={"SQLite":0 if integrity=="ok" else 1,"Foreign keys":len(foreign)};checks.update({k:int(c.execute(q).fetchone()[0]) for k,q in queries.items()});return {"integrity":integrity,"foreign_key_errors":[tuple(r) for r in foreign],"checks":checks}
