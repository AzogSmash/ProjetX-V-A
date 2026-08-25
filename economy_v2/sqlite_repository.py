from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from economy_v2.ai_config import AI_EFFICIENCY_PERCENT, ai_is_needed
from economy_v2.admin_money_config import MAX_ADMIN_CREDIT_AMOUNT, SQLITE_INTEGER_MAX
from economy_v2.ai_economy_config import AI_BOOTSTRAP_CREDITS, AI_ORE_RATE_PER_HOUR, AI_STORAGE_CAPACITY, get_ai_unit_price
from economy_v2.database import connect_database, immediate_transaction, initialize_database_sync
from economy_v2.delivery_config import get_delivery_cooldown_seconds, get_delivery_level, get_delivery_reduction_seconds, get_delivery_xp, get_max_delivery_commission
from economy_v2.forge_config import MAX_FORGE_UPGRADE_LEVEL, get_forge_count, get_forge_duration_seconds, get_forge_storage_capacity, get_forge_upgrade_cost
from economy_v2.merchant_config import MAX_MERCHANT_UPGRADE_LEVEL, get_merchant_upgrade_cost, get_trip_duration_seconds, get_truck_capacity
from economy_v2.mining_config import MAX_MINE_UPGRADE_LEVEL, get_production_rate, get_storage_capacity, get_upgrade_cost
from economy_v2.models import (AdminCreditResult, Banker, Blacksmith, DeliveryMission, DeliveryProfile, ForgeCollectionResult, ForgeJob, ForgeProcessResult, ForgeUpgradeResult, IndustrialActor, IndustrialCompany, IndustrialContract, IndustrialTransport, IndustrialUser, IngotShipment, InventoryEntry, MarketOrder, MarketOrderResult, MarketSummary, Merchant, MerchantTransportResult, MerchantUpgradeResult, Mine, MineCollectionResult, MineUpgradeResult, ShipmentResult, WorldSale)
from economy_v2.world_market_config import bounded_world_price


def _now() -> int:
    return int(time.time())


class SQLiteIndustrialEconomyRepository:
    """Repository SQLite local ; les mutations critiques utilisent BEGIN IMMEDIATE."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = initialize_database_sync(database_path)

    @contextmanager
    def _read(self):
        connection = connect_database(self.database_path)
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _user(r): return IndustrialUser(int(r["discord_user_id"]), int(r["credits"]), r["primary_job"])
    @staticmethod
    def _company(r): return IndustrialCompany(int(r["id"]), int(r["owner_discord_user_id"]), r["name"], r["job_type"], int(r["level"]), bool(r["is_first_company"]))
    @staticmethod
    def _order(r): return MarketOrder(int(r["id"]), int(r["owner_discord_user_id"]), r["side"], r["resource_type"], int(r["original_quantity"]), int(r["remaining_quantity"]), int(r["unit_price"]), r["status"], str(r["created_at"]))
    @staticmethod
    def _forge_job(r): return ForgeJob(int(r["id"]), int(r["owner_discord_user_id"]), int(r["company_id"]), int(r["forge_slot"]), r["resource_input"], r["resource_output"], int(r["input_quantity"]), int(r["output_quantity"]), str(r["started_at"]), str(r["finishes_at"]), r["status"])
    @staticmethod
    def _contract(r): return IndustrialContract(int(r["id"]), int(r["creator_discord_user_id"]), int(r["accepter_discord_user_id"]) if r["accepter_discord_user_id"] is not None else None, r["resource_type"], int(r["quantity"]), int(r["total_price"]), r["status"], str(r["expires_at"]))

    def _ensure_user(self, c, user_id):
        c.execute("INSERT OR IGNORE INTO industrial_users(discord_user_id) VALUES(?)", (user_id,))
        c.execute("INSERT OR IGNORE INTO industrial_actors(actor_type,discord_user_id) VALUES('player',?)", (user_id,))
        return c.execute("SELECT * FROM industrial_users WHERE discord_user_id=?", (user_id,)).fetchone()

    def _actor_id(self, c, user_id):
        self._ensure_user(c, user_id)
        return int(c.execute("SELECT id FROM industrial_actors WHERE discord_user_id=?", (user_id,)).fetchone()[0])

    def _company_for_job(self, c, user_id, job):
        return c.execute("SELECT * FROM industrial_companies WHERE owner_discord_user_id=? AND job_type=? AND is_first_company=1", (user_id, job)).fetchone()

    def _inventory(self, c, user_id, resource):
        r = c.execute("SELECT quantity FROM industrial_inventory WHERE owner_discord_user_id=? AND resource_type=?", (user_id, resource)).fetchone()
        return int(r[0]) if r else 0

    def _add_inventory(self, c, user_id, resource, amount):
        actor = self._actor_id(c, user_id)
        c.execute("INSERT INTO industrial_inventory(actor_id,owner_discord_user_id,resource_type,quantity,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(actor_id,resource_type) DO UPDATE SET quantity=quantity+excluded.quantity,updated_at=excluded.updated_at", (actor, user_id, resource, amount, _now()))
        return self._inventory(c, user_id, resource)

    def get_or_create_user(self, user_id):
        with immediate_transaction(self.database_path) as c: return self._user(self._ensure_user(c, user_id))

    def get_or_create_player_actor(self, user_id):
        with immediate_transaction(self.database_path) as c: return IndustrialActor(self._actor_id(c, user_id), "player", user_id, None)

    def get_primary_company(self, user_id):
        with self._read() as c:
            r = c.execute("SELECT * FROM industrial_companies WHERE owner_discord_user_id=? AND is_first_company=1", (user_id,)).fetchone()
            return self._company(r) if r else None

    def create_first_company(self, user_id, name, job_type):
        with immediate_transaction(self.database_path) as c:
            user = self._ensure_user(c, user_id)
            if c.execute("SELECT 1 FROM industrial_companies WHERE owner_discord_user_id=?", (user_id,)).fetchone(): return "already_exists", None
            if user["primary_job"] not in (None, job_type): raise ValueError("primary job mismatch")
            c.execute("UPDATE industrial_users SET primary_job=?,updated_at=? WHERE discord_user_id=?", (job_type, _now(), user_id))
            cur = c.execute("INSERT INTO industrial_companies(owner_discord_user_id,name,job_type) VALUES(?,?,?)", (user_id, name, job_type))
            return "created", self._company(c.execute("SELECT * FROM industrial_companies WHERE id=?", (cur.lastrowid,)).fetchone())

    def _refresh_mine(self, c, user_id):
        user = self._ensure_user(c, user_id); job = user["primary_job"]
        if job != "miner": return "not_miner", job, None
        company = self._company_for_job(c, user_id, "miner")
        if not company: return "no_miner_company", job, None
        c.execute("INSERT OR IGNORE INTO industrial_mines(owner_discord_user_id,company_id) VALUES(?,?)", (user_id, company["id"]))
        r = c.execute("SELECT * FROM industrial_mines WHERE owner_discord_user_id=?", (user_id,)).fetchone(); now = _now()
        previous_stock = int(r["stock"]); elapsed = max(0, now-int(r["last_production_at"])); progress = int(r["production_progress"])+elapsed*get_production_rate(int(r["production_level"])); capacity = get_storage_capacity(int(r["storage_level"])); stock = min(capacity, previous_stock+progress//3600); remainder = 0 if stock >= capacity else progress % 3600
        c.execute("UPDATE industrial_mines SET stock=?,production_progress=?,last_production_at=?,updated_at=? WHERE owner_discord_user_id=?", (stock, remainder, now, now, user_id))
        if stock > previous_stock:
            c.execute("INSERT INTO industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity) VALUES(?,'player','mine_production','iron_ore',?)", (self._actor_id(c,user_id), stock-previous_stock))
        r = c.execute("SELECT * FROM industrial_mines WHERE owner_discord_user_id=?", (user_id,)).fetchone()
        return "ok", job, Mine(user_id, int(company["id"]), company["name"], "iron_ore", int(r["stock"]), int(r["storage_level"]), int(r["production_level"]), int(r["quality_level"]), int(r["production_progress"]), str(r["last_production_at"]))

    def get_or_create_and_refresh_mine(self, user_id):
        with immediate_transaction(self.database_path) as c: return self._refresh_mine(c, user_id)

    def collect_mine(self, user_id):
        with immediate_transaction(self.database_path) as c:
            status, job, mine = self._refresh_mine(c, user_id)
            if status != "ok": return status, job, None
            total = self._add_inventory(c, user_id, "iron_ore", mine.stock)
            c.execute("UPDATE industrial_mines SET stock=0,updated_at=? WHERE owner_discord_user_id=?", (_now(), user_id))
            empty = Mine(mine.owner_discord_user_id, mine.company_id, mine.company_name, mine.resource_type, 0, mine.storage_level, mine.production_level, mine.quality_level, mine.production_progress, mine.last_production_at)
            return "ok", job, MineCollectionResult(empty, mine.stock, InventoryEntry(user_id, "iron_ore", total))

    def upgrade_mine(self, user_id, upgrade_type, request_id=None):
        with immediate_transaction(self.database_path) as c:
            status, job, mine = self._refresh_mine(c, user_id)
            if status != "ok": return status, job, None, None, None
            if request_id:
                old_request = c.execute("SELECT * FROM industrial_mine_upgrade_requests WHERE request_id=?", (request_id,)).fetchone()
                if old_request:
                    if old_request["owner_discord_user_id"] != user_id or old_request["upgrade_type"] != upgrade_type: raise ValueError("request id parameter mismatch")
                    old = int(old_request["previous_level"]); cost = int(old_request["upgrade_cost"]); balance = int(old_request["wallet_balance"])
                    return "ok", job, cost, balance, MineUpgradeResult(mine, upgrade_type, old, old+1, cost, balance)
            column = {"storage":"storage_level", "production":"production_level", "quality":"quality_level"}[upgrade_type]; old = int(getattr(mine, column)); balance = int(c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=?", (user_id,)).fetchone()[0])
            if old >= MAX_MINE_UPGRADE_LEVEL: return "max_level", job, None, balance, None
            cost = get_upgrade_cost(upgrade_type, old)
            if balance < cost: return "insufficient_funds", job, cost, balance, None
            balance -= cost; c.execute(f"UPDATE industrial_mines SET {column}={column}+1 WHERE owner_discord_user_id=?", (user_id,)); c.execute("UPDATE industrial_users SET credits=? WHERE discord_user_id=?", (balance, user_id))
            reference_id = None
            if request_id:
                c.execute("INSERT INTO industrial_mine_upgrade_requests VALUES(?,?,?,?,?,?,?,?)", (request_id,user_id,upgrade_type,old,old+1,cost,balance,_now()))
                reference_id = c.execute("SELECT rowid FROM industrial_mine_upgrade_requests WHERE request_id=?", (request_id,)).fetchone()[0]
            c.execute("INSERT INTO industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id) VALUES('mine_upgrade','sink',?,?,'mine_upgrade',?)", (self._actor_id(c,user_id),cost,reference_id))
            _,_,updated = self._refresh_mine(c,user_id)
            return "ok",job,cost,balance,MineUpgradeResult(updated,upgrade_type,old,old+1,cost,balance)

    def get_inventory(self, user_id):
        with self._read() as c: return [InventoryEntry(user_id,r["resource_type"],int(r["quantity"])) for r in c.execute("SELECT * FROM industrial_inventory WHERE owner_discord_user_id=? ORDER BY resource_type",(user_id,))]

    def create_market_order(self,user_id,side,resource_type,quantity,unit_price,request_id):
        with immediate_transaction(self.database_path) as c:
            user=self._ensure_user(c,user_id); old=c.execute("SELECT * FROM industrial_market_orders WHERE request_id=?",(request_id,)).fetchone()
            if old:
                if (old["owner_discord_user_id"],old["side"],old["resource_type"],old["original_quantity"],old["unit_price"])!=(user_id,side,resource_type,quantity,unit_price): raise ValueError("request id parameter mismatch")
                return "duplicate",MarketOrderResult(self._order(old),int(old["original_quantity"])-int(old["remaining_quantity"]),True),None
            role="miner" if side=="sell" else "merchant"
            if user["primary_job"]!=role:return f"not_{role}",None,None
            if c.execute("SELECT count(*) FROM industrial_market_orders WHERE owner_discord_user_id=? AND status='open'",(user_id,)).fetchone()[0]>=20:return "order_limit",None,None
            total=quantity*unit_price
            if side=="sell":
                available=self._inventory(c,user_id,resource_type)
                if available<quantity:return "insufficient_inventory",None,available
                c.execute("UPDATE industrial_inventory SET quantity=quantity-? WHERE owner_discord_user_id=? AND resource_type=?",(quantity,user_id,resource_type))
            else:
                available=int(user["credits"])
                if available<total:return "insufficient_funds",None,available
                c.execute("UPDATE industrial_users SET credits=credits-? WHERE discord_user_id=?",(total,user_id))
            actor=self._actor_id(c,user_id);company=self._company_for_job(c,user_id,role);now=_now();cur=c.execute("INSERT INTO industrial_market_orders(owner_actor_id,owner_discord_user_id,company_id,side,resource_type,original_quantity,remaining_quantity,unit_price,escrow_quantity,escrow_credits,request_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(actor,user_id,company["id"],side,resource_type,quantity,quantity,unit_price,quantity if side=="sell" else 0,total if side=="buy" else 0,request_id,now,now));oid=cur.lastrowid
            while True:
                incoming=c.execute("SELECT * FROM industrial_market_orders WHERE id=?",(oid,)).fetchone()
                if incoming["remaining_quantity"]==0:break
                other="sell" if side=="buy" else "buy";comp="<=" if side=="buy" else ">=";direction="ASC" if side=="buy" else "DESC"
                counter=c.execute(f"SELECT * FROM industrial_market_orders WHERE resource_type=? AND side=? AND status='open' AND unit_price {comp} ? AND owner_actor_id<>? ORDER BY unit_price {direction},created_at,id LIMIT 1",(resource_type,other,unit_price,actor)).fetchone()
                if not counter:break
                buy=incoming if side=="buy" else counter;sell=counter if side=="buy" else incoming;fill=min(int(buy["remaining_quantity"]),int(sell["remaining_quantity"]));price=int(counter["unit_price"]);trade=fill*price;refund=fill*(int(buy["unit_price"])-price)
                c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(trade,sell["owner_discord_user_id"]));
                if refund:c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(refund,buy["owner_discord_user_id"]))
                self._add_inventory(c,int(buy["owner_discord_user_id"]),resource_type,fill)
                for target in (buy,sell):
                    remaining=int(target["remaining_quantity"])-fill;c.execute("UPDATE industrial_market_orders SET remaining_quantity=?,escrow_quantity=CASE WHEN side='sell' THEN ? ELSE 0 END,escrow_credits=CASE WHEN side='buy' THEN ?*unit_price ELSE 0 END,status=?,closed_at=?,updated_at=? WHERE id=?",(remaining,remaining,remaining,"filled" if remaining==0 else "open",_now() if remaining==0 else None,_now(),target["id"]))
                tr=c.execute("INSERT INTO industrial_market_trades(resource_type,quantity,unit_price,total_price,seller_actor_id,buyer_actor_id,seller_discord_user_id,buyer_discord_user_id,sell_order_id,buy_order_id) VALUES(?,?,?,?,?,?,?,?,?,?)",(resource_type,fill,price,trade,sell["owner_actor_id"],buy["owner_actor_id"],sell["owner_discord_user_id"],buy["owner_discord_user_id"],sell["id"],buy["id"]));c.execute("INSERT INTO industrial_transactions(transaction_type,monetary_effect,actor_id,counterparty_actor_id,resource_type,quantity,credits,reference_type,reference_id) VALUES('market_trade','transfer',?,?,?,?,?,'market_trade',?)",(sell["owner_actor_id"],buy["owner_actor_id"],resource_type,fill,trade,tr.lastrowid))
            final=c.execute("SELECT * FROM industrial_market_orders WHERE id=?",(oid,)).fetchone();return "ok",MarketOrderResult(self._order(final),quantity-int(final["remaining_quantity"])),None

    def cancel_market_order(self,user_id,order_id):
        with immediate_transaction(self.database_path) as c:
            r=c.execute("SELECT * FROM industrial_market_orders WHERE id=? AND owner_discord_user_id=?",(order_id,user_id)).fetchone()
            if not r:return "not_found",None
            if r["status"]!="open":return "already_closed",None
            if r["side"]=="sell":self._add_inventory(c,user_id,r["resource_type"],r["escrow_quantity"])
            else:c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(r["escrow_credits"],user_id))
            c.execute("UPDATE industrial_market_orders SET remaining_quantity=0,escrow_quantity=0,escrow_credits=0,status='cancelled',closed_at=? WHERE id=?",(_now(),order_id));return "ok",self._order(c.execute("SELECT * FROM industrial_market_orders WHERE id=?",(order_id,)).fetchone())

    def get_market_orders(self,user_id):
        with self._read() as c:return [self._order(r) for r in c.execute("SELECT * FROM industrial_market_orders WHERE owner_discord_user_id=? AND status='open' ORDER BY created_at",(user_id,))]

    def get_market_summary(self,resource_type,depth):
        with self._read() as c:
            s=c.execute("SELECT sum(total_price),sum(quantity),min(unit_price),max(unit_price) FROM industrial_market_trades WHERE resource_type=? AND created_at>=?",(resource_type,_now()-86400)).fetchone();avg=float(s[0]/s[1]) if s[1] else None;sells=tuple(self._order(r) for r in c.execute("SELECT * FROM industrial_market_orders WHERE resource_type=? AND side='sell' AND status='open' ORDER BY unit_price,created_at LIMIT ?",(resource_type,depth)));buys=tuple(self._order(r) for r in c.execute("SELECT * FROM industrial_market_orders WHERE resource_type=? AND side='buy' AND status='open' ORDER BY unit_price DESC,created_at LIMIT ?",(resource_type,depth)));return MarketSummary(resource_type,avg,int(s[2]) if s[2] is not None else None,int(s[3]) if s[3] is not None else None,int(s[1] or 0),sells,buys)

    def _transport(self,r):
        return IndustrialTransport(int(r["id"]),int(r["sender_company_id"] or 0),int(r["receiver_company_id"] or 0),r["receiver_company_name"] or "Entreprise IA",int(r["merchant_discord_user_id"] or 0),r["resource_type"],int(r["quantity"]),str(r["departure_at"]),str(r["arrival_at"]),r["status"],int(r["truck_slot"]))

    def _refresh_transports(self,c,receiver_user=None):
        sql="SELECT * FROM industrial_transports WHERE status='in_transit' AND arrival_at<=?";args=[_now()]
        if receiver_user is not None:sql+=" AND receiver_actor_id=(SELECT id FROM industrial_actors WHERE discord_user_id=?)";args.append(receiver_user)
        for t in c.execute(sql,args).fetchall():
            receiver=c.execute("SELECT discord_user_id FROM industrial_actors WHERE id=?",(t["receiver_actor_id"],)).fetchone()
            if receiver and receiver[0] is not None:self._add_inventory(c,int(receiver[0]),t["resource_type"],int(t["quantity"]))
            mission=c.execute("SELECT * FROM industrial_delivery_missions WHERE transport_id=?",(t["id"],)).fetchone()
            if mission and mission["status"]=="open":
                merchant=c.execute("SELECT discord_user_id FROM industrial_actors WHERE id=?",(mission["merchant_actor_id"],)).fetchone()
                if merchant and merchant[0] is not None:c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(mission["commission_max"],merchant[0]))
                c.execute("UPDATE industrial_delivery_missions SET status='refunded',escrow_remaining=0,commission_paid=0,merchant_refund=commission_max,refunded_at=? WHERE id=?",(_now(),mission["id"]))
            c.execute("UPDATE industrial_transports SET status='delivered',completed_at=? WHERE id=?",(_now(),t["id"]))

    def _merchant(self,c,user_id):
        r=c.execute("SELECT m.*,co.name company_name,(SELECT count(*) FROM industrial_transports t JOIN industrial_actors a2 ON a2.id=t.operator_actor_id WHERE a2.discord_user_id=m.owner_discord_user_id AND t.status='in_transit') active_transports FROM industrial_merchants m JOIN industrial_companies co ON co.id=m.company_id WHERE m.owner_discord_user_id=?",(user_id,)).fetchone();return Merchant(user_id,int(r["company_id"]),r["company_name"],int(r["truck_count"]),int(r["truck_capacity_level"]),int(r["truck_speed_level"]),int(r["warehouse_level"]),int(r["active_transports"]))

    def _ensure_merchant(self,c,user_id):
        u=self._ensure_user(c,user_id);job=u["primary_job"]
        if job!="merchant":return "not_merchant",job,None
        company=self._company_for_job(c,user_id,"merchant")
        if not company:return "no_merchant_company",job,None
        c.execute("INSERT OR IGNORE INTO industrial_merchants(owner_discord_user_id,company_id) VALUES(?,?)",(user_id,company["id"]));return "ok",job,self._merchant(c,user_id)

    def get_or_create_merchant(self,user_id):
        with immediate_transaction(self.database_path) as c:self._refresh_transports(c);return self._ensure_merchant(c,user_id)

    def upgrade_merchant(self,user_id,upgrade_type,request_id):
        with immediate_transaction(self.database_path) as c:
            status,job,m=self._ensure_merchant(c,user_id)
            if status!="ok":return status,job,None,None,None
            previous=c.execute("SELECT * FROM industrial_merchant_upgrades WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if int(previous["owner_discord_user_id"])!=user_id or previous["upgrade_type"]!=upgrade_type:raise ValueError("request id parameter mismatch")
                return "duplicate",job,int(previous["cost"]),int(previous["balance_after"]),MerchantUpgradeResult(m,upgrade_type,int(previous["previous_level"]),int(previous["new_level"]),int(previous["cost"]),int(previous["balance_after"]),True)
            column={"trucks":"truck_count","capacity":"truck_capacity_level","speed":"truck_speed_level","warehouse":"warehouse_level"}[upgrade_type];old=int(getattr(m,column));balance=int(c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=?",(user_id,)).fetchone()[0]);cost=get_merchant_upgrade_cost(upgrade_type,old)
            if old>=MAX_MERCHANT_UPGRADE_LEVEL:return "max_level",job,None,balance,None
            if balance<cost:return "insufficient_funds",job,cost,balance,None
            balance-=cost;c.execute(f"UPDATE industrial_merchants SET {column}={column}+1 WHERE owner_discord_user_id=?",(user_id,));c.execute("UPDATE industrial_users SET credits=? WHERE discord_user_id=?",(balance,user_id));cur=c.execute("INSERT INTO industrial_merchant_upgrades(owner_discord_user_id,upgrade_type,previous_level,new_level,cost,balance_after,request_id) VALUES(?,?,?,?,?,?,?)",(user_id,upgrade_type,old,old+1,cost,balance,request_id));c.execute("INSERT INTO industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id) VALUES('merchant_upgrade','sink',?,?,'merchant_upgrade',?)",(self._actor_id(c,user_id),cost,cur.lastrowid));return "ok",job,cost,balance,MerchantUpgradeResult(self._merchant(c,user_id),upgrade_type,old,old+1,cost,balance)

    def _create_transport(self,c,user_id,receiver_id,resource,quantity,request_id,kind,sender_company,receiver_company,deduct=True):
        existing=c.execute("SELECT t.*,co.name receiver_company_name FROM industrial_transports t LEFT JOIN industrial_companies co ON co.id=t.receiver_company_id WHERE t.request_id=?",(request_id,)).fetchone()
        if existing:
            expected_receiver=self._actor_id(c,receiver_id)
            if (int(existing["merchant_discord_user_id"] or 0),int(existing["receiver_actor_id"]),existing["resource_type"],int(existing["quantity"]),existing["transport_type"])!=(user_id,expected_receiver,resource,quantity,kind):raise ValueError("request id parameter mismatch")
            return "duplicate",None,MerchantTransportResult(self._transport(existing),True)
        m=c.execute("SELECT * FROM industrial_merchants WHERE owner_discord_user_id=?",(user_id,)).fetchone();operator=self._actor_id(c,user_id);receiver=self._actor_id(c,receiver_id)
        if quantity>get_truck_capacity(int(m["truck_capacity_level"])):return "capacity_exceeded",None,None
        used={int(r[0]) for r in c.execute("SELECT truck_slot FROM industrial_transports WHERE operator_actor_id=? AND status='in_transit'",(operator,))};slot=next((x for x in range(1,int(m["truck_count"])+1) if x not in used),None)
        if slot is None:return "no_truck_available",None,None
        available=self._inventory(c,user_id,resource)
        if deduct and available<quantity:return "insufficient_inventory",available,None
        fee=get_max_delivery_commission(quantity);balance=int(c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=?",(user_id,)).fetchone()[0])
        if balance<fee:return "insufficient_commission_funds",balance,None
        if deduct:c.execute("UPDATE industrial_inventory SET quantity=quantity-? WHERE owner_discord_user_id=? AND resource_type=?",(quantity,user_id,resource))
        c.execute("UPDATE industrial_users SET credits=credits-? WHERE discord_user_id=?",(fee,user_id));now=_now();duration=get_trip_duration_seconds(int(m["truck_speed_level"]));cur=c.execute("INSERT INTO industrial_transports(sender_actor_id,receiver_actor_id,operator_actor_id,sender_company_id,receiver_company_id,merchant_discord_user_id,transport_type,resource_type,quantity,departure_at,arrival_at,original_duration_seconds,current_duration_seconds,truck_slot,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(operator,receiver,operator,sender_company,receiver_company,user_id,kind,resource,quantity,now,now+duration,duration,duration,slot,request_id));c.execute("INSERT INTO industrial_delivery_missions(transport_id,merchant_actor_id,merchant_discord_user_id,resource_type,quantity,commission_max,escrow_remaining) VALUES(?,?,?,?,?,?,?)",(cur.lastrowid,operator,user_id,resource,quantity,fee,fee));row=c.execute("SELECT t.*,co.name receiver_company_name FROM industrial_transports t LEFT JOIN industrial_companies co ON co.id=t.receiver_company_id WHERE t.id=?",(cur.lastrowid,)).fetchone();return "ok",available-quantity if deduct else available,MerchantTransportResult(self._transport(row))

    def start_transport(self,user_id,receiver_user_id,resource_type,quantity,request_id):
        with immediate_transaction(self.database_path) as c:
            status,job,m=self._ensure_merchant(c,user_id)
            if status!="ok":return status,job,None,None
            receiver=self._company_for_job(c,receiver_user_id,"blacksmith")
            if not receiver:return "invalid_receiver",job,None,None
            status,available,result=self._create_transport(c,user_id,receiver_user_id,resource_type,quantity,request_id,"ore_to_blacksmith",m.company_id,receiver["id"]);return status,job,available,result

    def get_merchant_transports(self,user_id):
        with immediate_transaction(self.database_path) as c:
            status,job,_=self._ensure_merchant(c,user_id)
            if status!="ok":return status,job,[]
            self._refresh_transports(c);rows=c.execute("SELECT t.*,co.name receiver_company_name FROM industrial_transports t LEFT JOIN industrial_companies co ON co.id=t.receiver_company_id WHERE t.merchant_discord_user_id=? ORDER BY t.id DESC LIMIT 20",(user_id,)).fetchall();return "ok",job,[self._transport(r) for r in rows]

    def _blacksmith(self,c,user_id):
        self._refresh_transports(c,user_id);now=_now();c.execute("UPDATE industrial_forge_jobs SET status='completed',completed_at=? WHERE owner_discord_user_id=? AND status='processing' AND finishes_at<=?",(now,user_id,now));r=c.execute("SELECT b.*,co.name company_name,(SELECT count(*) FROM industrial_forge_jobs j WHERE j.owner_discord_user_id=b.owner_discord_user_id AND j.status='processing') active_jobs,(SELECT count(*) FROM industrial_forge_jobs j WHERE j.owner_discord_user_id=b.owner_discord_user_id AND j.status='completed') completed_jobs,(SELECT coalesce(sum(output_quantity),0) FROM industrial_forge_jobs j WHERE j.owner_discord_user_id=b.owner_discord_user_id AND j.status IN('processing','completed')) reserved_output FROM industrial_blacksmiths b JOIN industrial_companies co ON co.id=b.company_id WHERE b.owner_discord_user_id=?",(user_id,)).fetchone();return Blacksmith(user_id,int(r["company_id"]),r["company_name"],int(r["forge_level"]),int(r["speed_level"]),int(r["storage_level"]),int(r["yield_level"]),int(r["active_jobs"]),int(r["completed_jobs"]),int(r["reserved_output"]))

    def _ensure_blacksmith(self,c,user_id):
        u=self._ensure_user(c,user_id);job=u["primary_job"]
        if job!="blacksmith":return "not_blacksmith",job,None
        company=self._company_for_job(c,user_id,"blacksmith")
        if not company:return "no_blacksmith_company",job,None
        c.execute("INSERT OR IGNORE INTO industrial_blacksmiths(owner_discord_user_id,company_id) VALUES(?,?)",(user_id,company["id"]));return "ok",job,self._blacksmith(c,user_id)

    def get_or_create_blacksmith(self,user_id):
        with immediate_transaction(self.database_path) as c:return self._ensure_blacksmith(c,user_id)

    def start_forge_job(self,user_id,resource_type,quantity,request_id):
        with immediate_transaction(self.database_path) as c:
            status,job,b=self._ensure_blacksmith(c,user_id)
            if status!="ok":return status,job,None,None
            previous=c.execute("SELECT * FROM industrial_forge_jobs WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if (int(previous["owner_discord_user_id"]),previous["resource_input"],int(previous["input_quantity"]))!=(user_id,resource_type,quantity):raise ValueError("request id parameter mismatch")
                return "duplicate",job,None,ForgeProcessResult(self._forge_job(previous),self._inventory(c,user_id,resource_type),True)
            used={int(r[0]) for r in c.execute("SELECT forge_slot FROM industrial_forge_jobs WHERE owner_discord_user_id=? AND status='processing'",(user_id,))};slot=next((x for x in range(1,get_forge_count(b.forge_level)+1) if x not in used),None)
            if slot is None:return "no_forge_available",job,None,None
            available=self._inventory(c,user_id,resource_type)
            if available<quantity:return "insufficient_inventory",job,available,None
            if b.reserved_output+quantity>get_forge_storage_capacity(b.storage_level):return "storage_full",job,available,None
            now=_now();c.execute("UPDATE industrial_inventory SET quantity=quantity-? WHERE owner_discord_user_id=? AND resource_type=?",(quantity,user_id,resource_type));cur=c.execute("INSERT INTO industrial_forge_jobs(owner_discord_user_id,company_id,forge_slot,resource_input,resource_output,input_quantity,output_quantity,speed_level_at_start,yield_level_at_start,started_at,finishes_at,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(user_id,b.company_id,slot,resource_type,"iron_ingot",quantity,quantity,b.speed_level,b.yield_level,now,now+get_forge_duration_seconds(quantity,b.speed_level),request_id));row=c.execute("SELECT * FROM industrial_forge_jobs WHERE id=?",(cur.lastrowid,)).fetchone();return "ok",job,available-quantity,ForgeProcessResult(self._forge_job(row),available-quantity)

    def collect_forge_jobs(self,user_id,request_id):
        with immediate_transaction(self.database_path) as c:
            status,job,_=self._ensure_blacksmith(c,user_id)
            if status!="ok":return status,job,None
            previous=c.execute("SELECT * FROM industrial_forge_collections WHERE request_id=?",(request_id,)).fetchone()
            if previous:return "duplicate",job,ForgeCollectionResult(int(previous["collected_quantity"]),int(previous["inventory_quantity"]),True)
            total=int(c.execute("SELECT coalesce(sum(output_quantity),0) FROM industrial_forge_jobs WHERE owner_discord_user_id=? AND status='completed'",(user_id,)).fetchone()[0]);inventory=self._add_inventory(c,user_id,"iron_ingot",total);c.execute("UPDATE industrial_forge_jobs SET status='collected',collected_at=? WHERE owner_discord_user_id=? AND status='completed'",(_now(),user_id));c.execute("INSERT INTO industrial_forge_collections(owner_discord_user_id,collected_quantity,inventory_quantity,request_id) VALUES(?,?,?,?)",(user_id,total,inventory,request_id));
            if total:c.execute("INSERT INTO industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity) VALUES(?,'player','forge_production','iron_ingot',?)",(self._actor_id(c,user_id),total))
            return "ok",job,ForgeCollectionResult(total,inventory)

    def upgrade_forge(self,user_id,upgrade_type,request_id):
        with immediate_transaction(self.database_path) as c:
            status,job,b=self._ensure_blacksmith(c,user_id)
            if status!="ok":return status,job,None,None,None
            previous=c.execute("SELECT * FROM industrial_forge_upgrades WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if int(previous["owner_discord_user_id"])!=user_id or previous["upgrade_type"]!=upgrade_type:raise ValueError("request id parameter mismatch")
                return "duplicate",job,int(previous["cost"]),int(previous["balance_after"]),ForgeUpgradeResult(b,upgrade_type,int(previous["previous_level"]),int(previous["new_level"]),int(previous["cost"]),int(previous["balance_after"]),True)
            column={"forges":"forge_level","speed":"speed_level","storage":"storage_level","yield":"yield_level"}[upgrade_type];old=int(getattr(b,column));balance=int(c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=?",(user_id,)).fetchone()[0]);cost=get_forge_upgrade_cost(upgrade_type,old)
            if old>=MAX_FORGE_UPGRADE_LEVEL:return "max_level",job,None,balance,None
            if balance<cost:return "insufficient_funds",job,cost,balance,None
            balance-=cost;c.execute(f"UPDATE industrial_blacksmiths SET {column}={column}+1 WHERE owner_discord_user_id=?",(user_id,));c.execute("UPDATE industrial_users SET credits=? WHERE discord_user_id=?",(balance,user_id));c.execute("INSERT INTO industrial_forge_upgrades(owner_discord_user_id,upgrade_type,previous_level,new_level,cost,balance_after,request_id) VALUES(?,?,?,?,?,?,?)",(user_id,upgrade_type,old,old+1,cost,balance,request_id));return "ok",job,cost,balance,ForgeUpgradeResult(self._blacksmith(c,user_id),upgrade_type,old,old+1,cost,balance)

    def get_forge_jobs(self,user_id):
        with immediate_transaction(self.database_path) as c:
            status,job,_=self._ensure_blacksmith(c,user_id)
            if status!="ok":return status,job,[]
            return "ok",job,[self._forge_job(r) for r in c.execute("SELECT * FROM industrial_forge_jobs WHERE owner_discord_user_id=? ORDER BY id DESC LIMIT 20",(user_id,))]

    @staticmethod
    def _shipment(r):
        return IngotShipment(int(r["id"]),int(r["blacksmith_company_id"]),int(r["blacksmith_discord_user_id"]),int(r["merchant_company_id"]),int(r["merchant_discord_user_id"]),int(r["banker_company_id"]),int(r["banker_discord_user_id"]),r["resource_type"],int(r["quantity"]),r["status"],str(r["created_at"]),str(r["accepted_at"]) if r["accepted_at"] else None,str(r["cancelled_at"]) if r["cancelled_at"] else None)

    def create_ingot_shipment(self,user_id,merchant_id,banker_id,quantity,request_id):
        with immediate_transaction(self.database_path) as c:
            status,job,b=self._ensure_blacksmith(c,user_id)
            if status!="ok":return status,job,None,None
            previous=c.execute("SELECT * FROM industrial_ingot_shipments WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if (int(previous["blacksmith_discord_user_id"]),int(previous["merchant_discord_user_id"]),int(previous["banker_discord_user_id"]),int(previous["quantity"]))!=(user_id,merchant_id,banker_id,quantity):raise ValueError("request id parameter mismatch")
                return "duplicate",job,None,ShipmentResult(self._shipment(previous),None,True)
            merchant=self._company_for_job(c,merchant_id,"merchant");banker=self._company_for_job(c,banker_id,"banker")
            if not merchant:return "invalid_merchant",job,None,None
            if not banker:return "invalid_banker",job,None,None
            available=self._inventory(c,user_id,"iron_ingot")
            if available<quantity:return "insufficient_inventory",job,available,None
            c.execute("UPDATE industrial_inventory SET quantity=quantity-? WHERE owner_discord_user_id=? AND resource_type='iron_ingot'",(quantity,user_id));cur=c.execute("INSERT INTO industrial_ingot_shipments(blacksmith_company_id,blacksmith_discord_user_id,merchant_company_id,merchant_discord_user_id,banker_company_id,banker_discord_user_id,quantity,request_id) VALUES(?,?,?,?,?,?,?,?)",(b.company_id,user_id,merchant["id"],merchant_id,banker["id"],banker_id,quantity,request_id));return "ok",job,available-quantity,ShipmentResult(self._shipment(c.execute("SELECT * FROM industrial_ingot_shipments WHERE id=?",(cur.lastrowid,)).fetchone()))

    def cancel_ingot_shipment(self,user_id,shipment_id,request_id):
        with immediate_transaction(self.database_path) as c:
            r=c.execute("SELECT * FROM industrial_ingot_shipments WHERE id=?",(shipment_id,)).fetchone();job=self._ensure_user(c,user_id)["primary_job"]
            if not r:return "not_found",job,None
            if int(r["blacksmith_discord_user_id"])!=user_id:return "not_owner",job,None
            if r["status"]=="cancelled" and r["cancel_request_id"]==request_id:return "duplicate",job,ShipmentResult(self._shipment(r),None,True)
            if r["status"]!="pending":return "already_closed",job,None
            self._add_inventory(c,user_id,"iron_ingot",int(r["quantity"]));c.execute("UPDATE industrial_ingot_shipments SET status='cancelled',cancel_request_id=?,cancelled_at=?,updated_at=? WHERE id=?",(request_id,_now(),_now(),shipment_id));return "ok",job,ShipmentResult(self._shipment(c.execute("SELECT * FROM industrial_ingot_shipments WHERE id=?",(shipment_id,)).fetchone()))

    def accept_ingot_shipment(self,user_id,shipment_id,request_id):
        with immediate_transaction(self.database_path) as c:
            status,job,m=self._ensure_merchant(c,user_id)
            if status!="ok":return status,job,None,None
            r=c.execute("SELECT * FROM industrial_ingot_shipments WHERE id=?",(shipment_id,)).fetchone()
            if not r:return "not_found",job,None,None
            if int(r["merchant_discord_user_id"])!=user_id:return "wrong_merchant",job,None,None
            if r["status"]=="accepted" and r["accept_request_id"]==request_id:
                t=c.execute("SELECT t.*,co.name receiver_company_name FROM industrial_transports t LEFT JOIN industrial_companies co ON co.id=t.receiver_company_id WHERE t.id=?",(r["transport_id"],)).fetchone();return "duplicate",job,None,ShipmentResult(self._shipment(r),self._transport(t),True)
            if r["status"]!="pending":return "already_closed",job,None,None
            transport_status,available,result=self._create_transport(c,user_id,int(r["banker_discord_user_id"]),"iron_ingot",int(r["quantity"]),request_id,"ingot_to_banker",int(r["blacksmith_company_id"]),int(r["banker_company_id"]),False)
            if transport_status!="ok":return transport_status,job,available,None
            c.execute("UPDATE industrial_ingot_shipments SET status='accepted',accept_request_id=?,accepted_at=?,transport_id=?,updated_at=? WHERE id=?",(request_id,_now(),result.transport.id,_now(),shipment_id));return "ok",job,available,ShipmentResult(self._shipment(c.execute("SELECT * FROM industrial_ingot_shipments WHERE id=?",(shipment_id,)).fetchone()),result.transport)

    def get_or_create_banker(self,user_id):
        with immediate_transaction(self.database_path) as c:
            u=self._ensure_user(c,user_id);job=u["primary_job"]
            if job!="banker":return "not_banker",job,None
            company=self._company_for_job(c,user_id,"banker")
            if not company:return "no_banker_company",job,None
            c.execute("INSERT OR IGNORE INTO industrial_bankers(owner_discord_user_id,company_id) VALUES(?,?)",(user_id,company["id"]));self._refresh_transports(c,user_id);balance=int(c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=?",(user_id,)).fetchone()[0]);return "ok",job,Banker(user_id,int(company["id"]),company["name"],balance)

    def sell_world_ingots(self,user_id,quantity,request_id):
        with immediate_transaction(self.database_path) as c:
            u=self._ensure_user(c,user_id);job=u["primary_job"]
            if job!="banker":return "not_banker",job,None,None
            company=self._company_for_job(c,user_id,"banker")
            if not company:return "no_banker_company",job,None,None
            self._refresh_transports(c,user_id);previous=c.execute("SELECT * FROM industrial_world_sales WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if int(previous["banker_discord_user_id"])!=user_id or int(previous["quantity"])!=quantity:raise ValueError("request id parameter mismatch")
                return "duplicate",job,None,WorldSale(int(previous["id"]),int(previous["quantity"]),int(previous["unit_price"]),int(previous["total_credits"]),int(previous["balance_after"]),str(previous["created_at"]),True)
            available=self._inventory(c,user_id,"iron_ingot")
            if available<quantity:return "insufficient_inventory",job,available,None
            volume=int(c.execute("SELECT coalesce(sum(quantity),0) FROM industrial_world_sales WHERE created_at>=?",(_now()-86400,)).fetchone()[0]);price=bounded_world_price(volume);total=price*quantity;c.execute("UPDATE industrial_inventory SET quantity=quantity-? WHERE owner_discord_user_id=? AND resource_type='iron_ingot'",(quantity,user_id));c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(total,user_id));balance=int(c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=?",(user_id,)).fetchone()[0]);cur=c.execute("INSERT INTO industrial_world_sales(banker_discord_user_id,banker_company_id,quantity,unit_price,total_credits,balance_after,request_id) VALUES(?,?,?,?,?,?,?)",(user_id,company["id"],quantity,price,total,balance,request_id));c.execute("INSERT INTO industrial_transactions(transaction_type,monetary_effect,actor_id,resource_type,quantity,credits,reference_type,reference_id) VALUES('world_sale','source',?,'iron_ingot',?,?,'world_sale',?)",(self._actor_id(c,user_id),quantity,total,cur.lastrowid));return "ok",job,available-quantity,WorldSale(cur.lastrowid,quantity,price,total,balance,str(_now()))

    def get_world_market(self):
        with self._read() as c:
            r=c.execute("SELECT coalesce(sum(quantity),0),min(unit_price),max(unit_price) FROM industrial_world_sales WHERE created_at>=?",(_now()-86400,)).fetchone();price=bounded_world_price(int(r[0]));return {"current_price":price,"volume_24h":int(r[0]),"low_24h":int(r[1] or price),"high_24h":int(r[2] or price),"change_percent":0.0}

    def get_world_sales(self,user_id):
        with self._read() as c:return [WorldSale(int(r["id"]),int(r["quantity"]),int(r["unit_price"]),int(r["total_credits"]),int(r["balance_after"]),str(r["created_at"])) for r in c.execute("SELECT * FROM industrial_world_sales WHERE banker_discord_user_id=? ORDER BY id DESC LIMIT 20",(user_id,))]

    def get_delivery_missions(self):
        with immediate_transaction(self.database_path) as c:
            self._refresh_transports(c);rows=c.execute("SELECT m.*,t.arrival_at FROM industrial_delivery_missions m JOIN industrial_transports t ON t.id=m.transport_id WHERE m.status='open' AND t.status='in_transit' ORDER BY m.id LIMIT 20").fetchall();return [DeliveryMission(int(r["id"]),int(r["transport_id"]),int(r["merchant_discord_user_id"]) if r["merchant_discord_user_id"] else None,int(r["merchant_actor_id"]),r["resource_type"],int(r["quantity"]),r["status"],int(r["commission_max"]),str(r["arrival_at"]),int(r["courier_discord_user_id"]) if r["courier_discord_user_id"] else None) for r in rows]

    def get_delivery_profile(self,user_id):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,user_id);c.execute("INSERT OR IGNORE INTO industrial_delivery_profiles(discord_user_id) VALUES(?)",(user_id,));r=c.execute("SELECT * FROM industrial_delivery_profiles WHERE discord_user_id=?",(user_id,)).fetchone();return DeliveryProfile(user_id,int(r["delivery_level"]),int(r["delivery_xp"]),int(r["completed_deliveries"]),str(r["delivery_cooldown_until"]) if r["delivery_cooldown_until"] else None)

    def accept_delivery(self,user_id,mission_id,request_id):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,user_id);m=c.execute("SELECT * FROM industrial_delivery_missions WHERE id=?",(mission_id,)).fetchone()
            if not m:return {"result_status":"not_found"}
            if m["status"]=="accepted" and m["accept_request_id"]==request_id:
                if int(m["courier_discord_user_id"])!=user_id:raise ValueError("request id parameter mismatch")
                return {"result_status":"duplicate","mission_id":mission_id,"commission_paid":int(m["commission_paid"])}
            if m["status"]!="open":return {"result_status":"already_taken"}
            t=c.execute("SELECT * FROM industrial_transports WHERE id=?",(m["transport_id"],)).fetchone();now=_now()
            if t["status"]!="in_transit" or int(t["arrival_at"])<=now:self._refresh_transports(c);return {"result_status":"arrived"}
            c.execute("INSERT OR IGNORE INTO industrial_delivery_profiles(discord_user_id) VALUES(?)",(user_id,));p=c.execute("SELECT * FROM industrial_delivery_profiles WHERE discord_user_id=?",(user_id,)).fetchone()
            if p["delivery_cooldown_until"] and int(p["delivery_cooldown_until"])>now:return {"result_status":"cooldown"}
            maximum=get_delivery_reduction_seconds(int(p["delivery_level"]));saved=min(maximum,max(0,int(t["arrival_at"])-now));paid=min(int(m["commission_max"]),int(m["commission_max"])*saved//maximum);refund=int(m["commission_max"])-paid;xp=get_delivery_xp(saved);level=get_delivery_level(int(p["delivery_xp"])+xp);cooldown=now+get_delivery_cooldown_seconds(level)
            c.execute("UPDATE industrial_transports SET arrival_at=max(?,arrival_at-?),current_duration_seconds=max(0,current_duration_seconds-?) WHERE id=?",(now,saved,saved,t["id"]));c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(paid,user_id));merchant=c.execute("SELECT discord_user_id FROM industrial_actors WHERE id=?",(m["merchant_actor_id"],)).fetchone()
            if merchant and merchant[0] is not None:c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(refund,merchant[0]))
            c.execute("UPDATE industrial_delivery_profiles SET delivery_xp=delivery_xp+?,delivery_level=?,completed_deliveries=completed_deliveries+1,delivery_cooldown_until=?,updated_at=? WHERE discord_user_id=?",(xp,level,cooldown,now,user_id));c.execute("UPDATE industrial_delivery_missions SET status='accepted',escrow_remaining=0,courier_discord_user_id=?,commission_paid=?,merchant_refund=?,saved_seconds=?,xp_awarded=?,accept_request_id=?,accepted_at=? WHERE id=?",(user_id,paid,refund,saved,xp,request_id,now,mission_id));return {"result_status":"ok","mission_id":mission_id,"saved_seconds":saved,"commission_paid":paid,"merchant_refund":refund,"xp_awarded":xp,"new_level":level,"cooldown_until":cooldown}

    def _expire_contracts(self,c):
        for r in c.execute("SELECT * FROM industrial_contracts WHERE status='open' AND expires_at<=?",(_now(),)).fetchall():
            c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(r["escrow_credits"],r["creator_discord_user_id"]));c.execute("UPDATE industrial_contracts SET status='expired',escrow_credits=0,cancelled_at=? WHERE id=?",(_now(),r["id"]))

    def create_contract(self,user_id,resource,quantity,total,request_id):
        with immediate_transaction(self.database_path) as c:
            u=self._ensure_user(c,user_id);self._expire_contracts(c);previous=c.execute("SELECT * FROM industrial_contracts WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if (previous["creator_discord_user_id"],previous["resource_type"],previous["quantity"],previous["total_price"])!=(user_id,resource,quantity,total):raise ValueError("request id parameter mismatch")
                return "duplicate",None,self._contract(previous)
            if c.execute("SELECT count(*) FROM industrial_contracts WHERE creator_discord_user_id=? AND status='open'",(user_id,)).fetchone()[0]>=10:return "contract_limit",None,None
            if int(u["credits"])<total:return "insufficient_funds",int(u["credits"]),None
            c.execute("UPDATE industrial_users SET credits=credits-? WHERE discord_user_id=?",(total,user_id));cur=c.execute("INSERT INTO industrial_contracts(creator_discord_user_id,resource_type,quantity,total_price,escrow_credits,request_id) VALUES(?,?,?,?,?,?)",(user_id,resource,quantity,total,total,request_id));return "ok",int(u["credits"])-total,self._contract(c.execute("SELECT * FROM industrial_contracts WHERE id=?",(cur.lastrowid,)).fetchone())

    def accept_contract(self,user_id,contract_id,request_id):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,user_id);self._expire_contracts(c);r=c.execute("SELECT * FROM industrial_contracts WHERE id=?",(contract_id,)).fetchone()
            if not r:return "not_found",None,None
            if r["status"]=="completed" and r["accept_request_id"]==request_id:return "duplicate",None,self._contract(r)
            if r["status"]!="open":return "already_closed",None,None
            if int(r["creator_discord_user_id"])==user_id:return "own_contract",None,None
            available=self._inventory(c,user_id,r["resource_type"])
            if available<int(r["quantity"]):return "insufficient_inventory",available,None
            c.execute("UPDATE industrial_inventory SET quantity=quantity-? WHERE owner_discord_user_id=? AND resource_type=?",(r["quantity"],user_id,r["resource_type"]));self._add_inventory(c,int(r["creator_discord_user_id"]),r["resource_type"],int(r["quantity"]));c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(r["escrow_credits"],user_id));c.execute("UPDATE industrial_contracts SET status='completed',escrow_credits=0,accepter_discord_user_id=?,accept_request_id=?,completed_at=? WHERE id=?",(user_id,request_id,_now(),contract_id));return "ok",available-int(r["quantity"]),self._contract(c.execute("SELECT * FROM industrial_contracts WHERE id=?",(contract_id,)).fetchone())

    def cancel_contract(self,user_id,contract_id,request_id):
        with immediate_transaction(self.database_path) as c:
            self._expire_contracts(c);r=c.execute("SELECT * FROM industrial_contracts WHERE id=?",(contract_id,)).fetchone()
            if not r:return "not_found",None
            if int(r["creator_discord_user_id"])!=user_id:return "not_owner",None
            if r["status"]=="cancelled" and r["cancel_request_id"]==request_id:return "duplicate",self._contract(r)
            if r["status"]!="open":return "already_closed",None
            c.execute("UPDATE industrial_users SET credits=credits+? WHERE discord_user_id=?",(r["escrow_credits"],user_id));c.execute("UPDATE industrial_contracts SET status='cancelled',escrow_credits=0,cancel_request_id=?,cancelled_at=? WHERE id=?",(request_id,_now(),contract_id));return "ok",self._contract(c.execute("SELECT * FROM industrial_contracts WHERE id=?",(contract_id,)).fetchone())

    def get_contracts(self,user_id,mine):
        with immediate_transaction(self.database_path) as c:
            self._expire_contracts(c);sql="SELECT * FROM industrial_contracts WHERE creator_discord_user_id=?" if mine else "SELECT * FROM industrial_contracts WHERE status='open'";args=(user_id,) if mine else ();return [self._contract(r) for r in c.execute(sql+" ORDER BY created_at DESC LIMIT 20",args)]

    def record_activity(self,user_id):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,user_id);c.execute("INSERT INTO industrial_user_activity(discord_user_id,last_active_at,command_count) VALUES(?,?,1) ON CONFLICT(discord_user_id) DO UPDATE SET last_active_at=excluded.last_active_at,command_count=command_count+1",(user_id,_now()))

    def adjust_admin_credits(self, admin_user_id, target_user_id, operation, amount, request_id):
        if not 1 <= admin_user_id <= SQLITE_INTEGER_MAX or not 1 <= target_user_id <= SQLITE_INTEGER_MAX:
            raise ValueError("invalid Discord user id")
        if operation not in {"add", "remove"} or not 1 <= amount <= MAX_ADMIN_CREDIT_AMOUNT:
            raise ValueError("invalid admin credit adjustment")
        if not request_id or len(request_id) > 80:
            raise ValueError("invalid admin credit request id")
        with immediate_transaction(self.database_path) as c:
            previous = c.execute(
                "SELECT * FROM industrial_admin_credit_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous:
                parameters = (
                    int(previous["admin_discord_user_id"]),
                    int(previous["target_discord_user_id"]),
                    previous["operation"],
                    int(previous["amount"]),
                )
                if parameters != (admin_user_id, target_user_id, operation, amount):
                    raise ValueError("admin credit request id parameter mismatch")
                return AdminCreditResult(
                    "ok", operation, admin_user_id, target_user_id, amount,
                    int(previous["balance_before"]), int(previous["balance_after"]),
                    request_id, True,
                )

            existing = c.execute(
                "SELECT credits FROM industrial_users WHERE discord_user_id=?",
                (target_user_id,),
            ).fetchone()
            balance_before = int(existing[0]) if existing else 0
            if operation == "add" and balance_before > SQLITE_INTEGER_MAX - amount:
                raise ValueError("industrial credit balance overflow")
            if operation == "remove" and balance_before < amount:
                return AdminCreditResult(
                    "insufficient_funds", operation, admin_user_id, target_user_id,
                    amount, balance_before, balance_before, request_id,
                )

            self._ensure_user(c, target_user_id)
            balance_after = balance_before + amount if operation == "add" else balance_before - amount
            c.execute(
                "UPDATE industrial_users SET credits=?,updated_at=? WHERE discord_user_id=?",
                (balance_after, _now(), target_user_id),
            )
            request = c.execute(
                "INSERT INTO industrial_admin_credit_requests("
                "request_id,admin_discord_user_id,target_discord_user_id,operation,amount,"
                "balance_before,balance_after) VALUES(?,?,?,?,?,?,?)",
                (request_id, admin_user_id, target_user_id, operation, amount,
                 balance_before, balance_after),
            )
            transaction_type = f"admin_credit_{operation}"
            monetary_effect = "source" if operation == "add" else "sink"
            metadata = json.dumps({
                "admin_discord_user_id": admin_user_id,
                "target_discord_user_id": target_user_id,
                "amount": amount,
                "balance_before": balance_before,
                "balance_after": balance_after,
                "request_id": request_id,
                "reason": None,
            }, separators=(",", ":"))
            c.execute(
                "INSERT INTO industrial_transactions("
                "transaction_type,monetary_effect,actor_id,credits,reference_type,"
                "reference_id,metadata) VALUES(?,?,?,?,?,?,?)",
                (transaction_type, monetary_effect, self._actor_id(c, target_user_id),
                 amount, "admin_credit_request", request.lastrowid, metadata),
            )
            return AdminCreditResult(
                "ok", operation, admin_user_id, target_user_id, amount,
                balance_before, balance_after, request_id,
            )

    def _ensure_ai(self,c):
        rows=[]
        for job,name in (("miner","Mines de Secours"),("merchant","Transit de Secours"),("blacksmith","Forges de Secours")):
            active=int(c.execute("SELECT count(*) FROM industrial_users u LEFT JOIN industrial_user_activity a ON a.discord_user_id=u.discord_user_id WHERE u.primary_job=? AND coalesce(a.last_active_at,0)>=?",(job,_now()-30*86400)).fetchone()[0]);enabled=int(ai_is_needed(active));c.execute("INSERT INTO industrial_ai_companies(name,job_type,enabled,efficiency_percent) VALUES(?,?,?,?) ON CONFLICT(job_type) DO UPDATE SET enabled=excluded.enabled,updated_at=unixepoch()",(name,job,enabled,AI_EFFICIENCY_PERCENT));company=c.execute("SELECT * FROM industrial_ai_companies WHERE job_type=?",(job,)).fetchone();rows.append(dict(company)|{"active_players":active})
            if enabled:
                c.execute("INSERT OR IGNORE INTO industrial_actors(actor_type,ai_company_id) VALUES('ai',?)",(company["id"],));actor=int(c.execute("SELECT id FROM industrial_actors WHERE ai_company_id=?",(company["id"],)).fetchone()[0]);existing=c.execute("SELECT 1 FROM industrial_ai_accounts WHERE actor_id=?",(actor,)).fetchone()
                if not existing:
                    c.execute("INSERT INTO industrial_ai_accounts(actor_id,credits) VALUES(?,?)",(actor,AI_BOOTSTRAP_CREDITS));event=c.execute("INSERT INTO industrial_ai_cash_events(actor_id,event_type,amount,balance_after,reference_type,reference_id) VALUES(?,'bootstrap_source',?,?, 'ai_company',?)",(actor,AI_BOOTSTRAP_CREDITS,AI_BOOTSTRAP_CREDITS,company["id"]));c.execute("INSERT INTO industrial_transactions(transaction_type,monetary_effect,actor_id,credits,reference_type,reference_id) VALUES('ai_bootstrap','source',? ,?,'ai_cash_event',?)",(actor,AI_BOOTSTRAP_CREDITS,event.lastrowid))
                if job in("miner","blacksmith"):c.execute("INSERT OR IGNORE INTO industrial_ai_production(actor_id,resource_type,rate_per_hour,capacity) VALUES(?,?,?,?)",(actor,"iron_ore" if job=="miner" else "iron_ingot",AI_ORE_RATE_PER_HOUR,AI_STORAGE_CAPACITY))
        return rows

    def evaluate_ai_companies(self):
        with immediate_transaction(self.database_path) as c:return self._ensure_ai(c)

    def _refresh_ai(self,c):
        for state in c.execute("SELECT * FROM industrial_ai_production").fetchall():
            now=_now();elapsed=max(0,now-int(state["last_production_at"]));progress=int(state["production_progress"])+elapsed*int(state["rate_per_hour"]);produced=progress//3600;row=c.execute("SELECT quantity FROM industrial_inventory WHERE actor_id=? AND resource_type=?",(state["actor_id"],state["resource_type"])).fetchone();available=int(row[0]) if row else 0;added=min(produced,max(0,int(state["capacity"])-available));c.execute("INSERT INTO industrial_inventory(actor_id,resource_type,quantity) VALUES(?,?,?) ON CONFLICT(actor_id,resource_type) DO UPDATE SET quantity=quantity+excluded.quantity",(state["actor_id"],state["resource_type"],added));c.execute("UPDATE industrial_ai_production SET total_produced=total_produced+?,production_progress=?,last_production_at=?,updated_at=? WHERE actor_id=?",(added,0 if available+added>=state["capacity"] else progress%3600,now,now,state["actor_id"]))
            if added:c.execute("INSERT INTO industrial_resource_events(actor_id,actor_type,event_type,resource_type,quantity) VALUES(?,'ai','mine_production',?,?)",(state["actor_id"],state["resource_type"],added))

    def purchase_ai_supply(self,user_id,resource_type,quantity,request_id):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,user_id);self._ensure_ai(c);self._refresh_ai(c);previous=c.execute("SELECT * FROM industrial_ai_supply_purchases WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if (int(previous["buyer_discord_user_id"]),previous["resource_type"],int(previous["quantity"]))!=(user_id,resource_type,quantity):raise ValueError("request id parameter mismatch")
                return {"result_status":"duplicate","purchase_id":int(previous["id"]),"quantity":int(previous["quantity"]),"total_price":int(previous["total_price"]),"transport_id":previous["transport_id"]}
            producer=c.execute("SELECT p.actor_id FROM industrial_ai_production p JOIN industrial_actors a ON a.id=p.actor_id JOIN industrial_ai_companies co ON co.id=a.ai_company_id WHERE p.resource_type=? AND co.enabled=1 LIMIT 1",(resource_type,)).fetchone()
            if not producer:return {"result_status":"ai_unavailable"}
            stock=c.execute("SELECT quantity FROM industrial_inventory WHERE actor_id=? AND resource_type=?",(producer[0],resource_type)).fetchone();available=int(stock[0]) if stock else 0;price=get_ai_unit_price(resource_type);total=price*quantity;balance=int(c.execute("SELECT credits FROM industrial_users WHERE discord_user_id=?",(user_id,)).fetchone()[0])
            if available<quantity:return {"result_status":"insufficient_inventory","available_amount":available}
            if balance<total:return {"result_status":"insufficient_funds","available_amount":balance}
            c.execute("UPDATE industrial_users SET credits=credits-? WHERE discord_user_id=?",(total,user_id));c.execute("UPDATE industrial_ai_accounts SET credits=credits+? WHERE actor_id=?",(total,producer[0]));c.execute("UPDATE industrial_inventory SET quantity=quantity-? WHERE actor_id=? AND resource_type=?",(quantity,producer[0],resource_type));self._add_inventory(c,user_id,resource_type,quantity);cur=c.execute("INSERT INTO industrial_ai_supply_purchases(buyer_discord_user_id,producer_actor_id,operator_actor_id,resource_type,quantity,total_price,request_id) VALUES(?,?,?,?,?,?,?)",(user_id,producer[0],producer[0],resource_type,quantity,total,request_id));return {"result_status":"ok","purchase_id":cur.lastrowid,"quantity":quantity,"unit_price":price,"total_price":total,"transport_id":None,"arrival_at":_now()}

    def get_admin_credit_stats(self):
        since = _now() - 86400
        with self._read() as c:
            rows = c.execute(
                "SELECT transaction_type,coalesce(sum(credits),0) AS total "
                "FROM industrial_transactions WHERE transaction_type IN("
                "'admin_credit_add','admin_credit_remove') AND created_at>=? "
                "GROUP BY transaction_type",
                (since,),
            ).fetchall()
        totals = {row["transaction_type"]: int(row["total"]) for row in rows}
        return {
            "admin_credit_sources": totals.get("admin_credit_add", 0),
            "admin_credit_sinks": totals.get("admin_credit_remove", 0),
        }

    def get_economy_stats(self):
        with immediate_transaction(self.database_path) as c:
            self._ensure_ai(c);self._refresh_ai(c);since=_now()-86400
            def scalar(sql,args=()):return c.execute(sql,args).fetchone()[0]
            def total(resource,kind):return int(scalar("SELECT coalesce(sum(quantity),0) FROM industrial_resource_events WHERE actor_type=? AND resource_type=? AND created_at>=?",(kind,resource,since)))
            player_ore,ai_ore=total("iron_ore","player"),total("iron_ore","ai");player_ingots,ai_ingots=total("iron_ingot","player"),total("iron_ingot","ai");avg=scalar("SELECT sum(total_price)*1.0/nullif(sum(quantity),0) FROM industrial_market_trades WHERE created_at>=?",(since,))
            return {"player_credits":int(scalar("SELECT coalesce(sum(credits),0) FROM industrial_users")),"ai_credits":int(scalar("SELECT coalesce(sum(credits),0) FROM industrial_ai_accounts")),"player_ore":player_ore,"ai_ore":ai_ore,"player_ingots":player_ingots,"ai_ingots":ai_ingots,"ai_ore_percent":100*ai_ore/max(1,player_ore+ai_ore),"ai_ingot_percent":100*ai_ingots/max(1,player_ingots+ai_ingots),"market_volume":int(scalar("SELECT coalesce(sum(quantity),0) FROM industrial_market_trades WHERE created_at>=?",(since,))),"market_average_price":float(avg or 0),"ai_market_percent":0.0,"active_transports":int(scalar("SELECT count(*) FROM industrial_transports WHERE status='in_transit'")),"average_delivery_minutes":float(scalar("SELECT coalesce(avg(current_duration_seconds),0)/60.0 FROM industrial_transports WHERE created_at>=?",(since,))),"ai_transport_percent":0.0,"world_price":bounded_world_price(int(scalar("SELECT coalesce(sum(quantity),0) FROM industrial_world_sales WHERE created_at>=?",(since,)))),"world_price_change_percent":0.0,"active_contracts":int(scalar("SELECT count(*) FROM industrial_contracts WHERE status='open' AND expires_at>?",(_now(),))),"player_actors":int(scalar("SELECT count(*) FROM industrial_actors WHERE actor_type='player'")),"ai_actors":int(scalar("SELECT count(*) FROM industrial_actors WHERE actor_type='ai'")),"active_player_companies":int(scalar("SELECT count(*) FROM industrial_companies")),"active_ai_companies":int(scalar("SELECT count(*) FROM industrial_ai_companies WHERE enabled=1")),"active_miners":int(scalar("SELECT count(*) FROM industrial_users WHERE primary_job='miner'")),"active_merchants":int(scalar("SELECT count(*) FROM industrial_users WHERE primary_job='merchant'")),"active_blacksmiths":int(scalar("SELECT count(*) FROM industrial_users WHERE primary_job='blacksmith'")),"active_bankers":int(scalar("SELECT count(*) FROM industrial_users WHERE primary_job='banker'"))}
