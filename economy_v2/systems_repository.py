from __future__ import annotations

import json
import time

from economy_v2.database import immediate_transaction

SEASON_SECONDS = 30 * 86400
EVENT_CYCLE_SECONDS = 6 * 3600
EVENT_HORIZON_SECONDS = 365 * 86400
EVENT_EXTENSION_THRESHOLD_SECONDS = 30 * 86400
EVENT_DEFINITIONS = (
    ("mining_rush", "⛏️ Ruée minière", 11500),
    ("industrial_boom", "🔨 Boom industriel", 11500),
    ("world_demand", "🏦 Demande mondiale", 11000),
    ("logistics_rush", "🚚 Rush logistique", 8500),
    ("delivery_bonus", "📦 Prime livraison", 11000),
)
ROLE_PERMISSIONS = {
    "owner": frozenset({"view_company","view_inventory","start_production","collect_output","prepare_shipment","manage_transport","view_orders","invite","remove_member","manage_roles"}),
    "manager": frozenset({"view_company","view_inventory","start_production","collect_output","prepare_shipment","manage_transport","view_orders","invite"}),
    "employee": frozenset({"view_company","view_inventory","view_orders"}),
}


def _now(): return int(time.time())


class IndustrialSystemsMixin:
    @staticmethod
    def _ensure_event_horizon(c,now):
        """Keep a deterministic year of persisted six-hour event cycles ahead.

        The cycle number fully determines the event, so extending the calendar is
        repeatable.  The caller holds BEGIN IMMEDIATE; the unique cycle_key makes
        the operation idempotent as an additional database-level safeguard.
        """
        now=int(now);current_cycle=now//EVENT_CYCLE_SECONDS
        latest=c.execute("SELECT max(starts_at) FROM industrial_economic_events WHERE cycle_key GLOB 'cycle:[0-9]*'").fetchone()[0]
        latest_cycle=int(latest)//EVENT_CYCLE_SECONDS if latest is not None else current_cycle-1
        latest_end=(latest_cycle+1)*EVENT_CYCLE_SECONDS
        if latest_end-now<=EVENT_EXTENSION_THRESHOLD_SECONDS:
            target_cycle=(now+EVENT_HORIZON_SECONDS)//EVENT_CYCLE_SECONDS
            for event_cycle in range(latest_cycle+1,target_cycle+1):
                kind,name,bps=EVENT_DEFINITIONS[event_cycle%len(EVENT_DEFINITIONS)];start=event_cycle*EVENT_CYCLE_SECONDS
                c.execute("INSERT OR IGNORE INTO industrial_economic_events(cycle_key,event_type,display_name,starts_at,ends_at,multiplier_bps,status) VALUES(?,?,?,?,?,?,?)",(f"cycle:{event_cycle}",kind,name,start,start+EVENT_CYCLE_SECONDS,bps,"active" if event_cycle==current_cycle else "upcoming"))
        c.execute("UPDATE industrial_economic_events SET status='finished' WHERE status IN('active','upcoming') AND ends_at<=?",(now,))
        c.execute("UPDATE industrial_economic_events SET status='active' WHERE cycle_key=? AND starts_at<=? AND ends_at>?",(f"cycle:{current_cycle}",now,now))
        row=c.execute("SELECT event_type,ends_at FROM industrial_economic_events WHERE cycle_key=?",(f"cycle:{current_cycle}",)).fetchone()
        if row:
            event_key=f"event:cycle:{current_cycle}"
            c.execute("INSERT INTO industrial_notification_events(discord_user_id,event_type,event_key,payload) SELECT u.discord_user_id,'event',?,? FROM industrial_users u WHERE NOT EXISTS (SELECT 1 FROM industrial_notification_events n WHERE n.discord_user_id=u.discord_user_id AND n.event_type='event' AND n.event_key=?)",(event_key,json.dumps({"event_type":row[0],"ends_at":int(row[1])}),event_key))

    def ensure_current_event(self, now=None):
        with immediate_transaction(self.database_path) as c:self._ensure_event_horizon(c,int(now or _now()))

    @staticmethod
    def _event_multiplier(c,event_type,now=None,ensure_horizon=False):
        now=int(now or _now())
        if ensure_horizon:IndustrialSystemsMixin._ensure_event_horizon(c,now)
        row=c.execute("SELECT multiplier_bps FROM industrial_economic_events WHERE event_type=? AND status IN('active','upcoming') AND starts_at<=? AND ends_at>? ORDER BY starts_at DESC LIMIT 1",(event_type,now,now)).fetchone();return int(row[0]) if row else 10000

    def get_active_events(self,now=None):
        now=int(now or _now())
        with immediate_transaction(self.database_path) as c:
            self._ensure_event_horizon(c,now)
            return [dict(r) for r in c.execute("SELECT event_type,display_name,starts_at,ends_at,multiplier_bps FROM industrial_economic_events WHERE status IN('active','upcoming') AND starts_at<=? AND ends_at>? ORDER BY ends_at",(now,now))]

    def _ensure_season(self,c,now):
        active=c.execute("SELECT * FROM industrial_seasons WHERE status='active'").fetchone()
        if active and int(active["ends_at"])<=now:
            self._finalize_season(c,active,now);active=None
        if not active:
            number=int(c.execute("SELECT coalesce(max(season_number),0)+1 FROM industrial_seasons").fetchone()[0]);start=now-now%86400
            c.execute("INSERT INTO industrial_seasons(season_number,name,starts_at,ends_at,status) VALUES(?,?,?,?, 'active')",(number,f"Saison industrielle {number}",start,start+SEASON_SECONDS));active=c.execute("SELECT * FROM industrial_seasons WHERE id=last_insert_rowid()").fetchone()
        return active

    def _score_user(self,c,season,uid):
        actor=c.execute("SELECT id FROM industrial_actors WHERE discord_user_id=?",(uid,)).fetchone()
        if not actor:return {}
        aid=int(actor[0]);start,end=int(season["starts_at"]),min(_now(),int(season["ends_at"]))
        scalar=lambda sql,args: int(c.execute(sql,args).fetchone()[0] or 0)
        mine=scalar("SELECT coalesce(sum(quantity),0) FROM industrial_resource_events WHERE actor_id=? AND event_type='mine_production' AND created_at BETWEEN ? AND ?",(aid,start,end))
        merchant=scalar("SELECT coalesce(sum(quantity),0) FROM industrial_transports WHERE operator_actor_id=? AND created_at BETWEEN ? AND ?",(aid,start,end))
        forge=scalar("SELECT coalesce(sum(output_quantity),0) FROM industrial_forge_jobs WHERE owner_discord_user_id=? AND started_at BETWEEN ? AND ?",(uid,start,end))
        bank=scalar("SELECT coalesce(sum(quantity),0) FROM industrial_world_sales WHERE banker_discord_user_id=? AND created_at BETWEEN ? AND ?",(uid,start,end))
        delivery=scalar("SELECT coalesce(sum(coalesce(saved_seconds,0))/60,0)+count(*)*10 FROM industrial_delivery_missions WHERE courier_discord_user_id=? AND status='accepted' AND accepted_at BETWEEN ? AND ?",(uid,start,end))
        contracts=scalar("SELECT count(*)*25 FROM industrial_contracts WHERE accepter_discord_user_id=? AND status='completed' AND completed_at BETWEEN ? AND ?",(uid,start,end))
        scores={"mine":mine,"merchant":merchant,"forge":forge*2,"bank":bank*2,"delivery":delivery,"contracts":contracts};scores["overall"]=sum(scores.values())
        for category,score in scores.items():c.execute("INSERT INTO industrial_season_scores(season_id,actor_id,discord_user_id,category,score,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(season_id,actor_id,category) DO UPDATE SET score=excluded.score,updated_at=excluded.updated_at WHERE industrial_season_scores.frozen=0",(season["id"],aid,uid,category,score,_now()))
        return scores

    def _finalize_season(self,c,season,now):
        if season["status"]!="active":return
        users=[int(r[0]) for r in c.execute("SELECT discord_user_id FROM industrial_users")]
        for uid in users:self._score_user(c,season,uid)
        c.execute("UPDATE industrial_season_scores SET frozen=1 WHERE season_id=?",(season["id"],))
        for category in ("overall","mine","merchant","forge","bank","delivery","contracts"):
            limit=10 if category=="overall" else 3
            for rank,row in enumerate(c.execute("SELECT discord_user_id FROM industrial_season_scores WHERE season_id=? AND category=? AND score>0 ORDER BY score DESC,actor_id LIMIT ?",(season["id"],category,limit)),1):
                uid=int(row[0]);slug=f"season-{season['season_number']}-{category}-{rank}";title=f"Champion {category} S{season['season_number']}" if rank==1 else f"Top {rank} {category} S{season['season_number']}"
                c.execute("INSERT OR IGNORE INTO industrial_titles(slug,display_name,description,source,rarity) VALUES(?,?,?,'season',?)",(slug,title,"Récompense de classement saisonnier.","legendary" if rank==1 else "epic"))
                tid=c.execute("SELECT id FROM industrial_titles WHERE slug=?",(slug,)).fetchone()[0]
                c.execute("INSERT OR IGNORE INTO industrial_user_titles(discord_user_id,title_id,source_reference) VALUES(?,?,?)",(uid,tid,f"season:{season['id']}:{category}:{rank}"))
                c.execute("INSERT OR IGNORE INTO industrial_season_rewards(season_id,discord_user_id,category,rank,reward_type,reward_value) VALUES(?,?,?,?, 'title',?)",(season["id"],uid,category,rank,slug))
                c.execute("INSERT OR IGNORE INTO industrial_reputation_events(discord_user_id,event_type,reputation,source_key) VALUES(?,'season',?,?)",(uid,10 if rank==1 else 5,f"season:{season['id']}:{category}:{rank}"))
                c.execute("INSERT OR IGNORE INTO industrial_notification_events(discord_user_id,event_type,event_key,payload) VALUES(?,'season',?,?)",(uid,f"season:{season['id']}:{category}:{rank}",json.dumps({"season":season["season_number"],"category":category,"rank":rank})))
        c.execute("UPDATE industrial_seasons SET status='finished',finished_at=? WHERE id=? AND status='active'",(now,season["id"]))

    def get_season_dashboard(self,uid,category="overall",now=None):
        if category not in {"overall","mine","merchant","forge","bank","delivery","contracts"}:raise ValueError("invalid season category")
        now=int(now or _now())
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,uid);season=self._ensure_season(c,now);scores=self._score_user(c,season,uid)
            ranking=[dict(r) for r in c.execute("SELECT discord_user_id,score FROM industrial_season_scores WHERE season_id=? AND category=? ORDER BY score DESC,actor_id LIMIT 10",(season["id"],category))]
            rank=int(c.execute("SELECT count(*)+1 FROM industrial_season_scores WHERE season_id=? AND category=? AND score>?",(season["id"],category,scores[category])).fetchone()[0])
            return {"season":dict(season),"scores":scores,"category":category,"rank":rank,"ranking":ranking}

    def get_season_history(self,limit=10):
        with self._read() as c:return [dict(r) for r in c.execute("SELECT * FROM industrial_seasons WHERE status='finished' ORDER BY season_number DESC LIMIT ?",(limit,))]

    def refresh_titles(self,uid):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,uid);m=self._metrics(c,uid);rules=(("master_miner",m["ore_produced"]>=1000),("road_ace",m["deliveries"]>=100),("master_smith",m["ingots_forged"]>=1000),("magnate",m["credits"]>=1000000),("market_shark",m["market_volume"]>=100000),("negotiator",m["contracts_completed"]>=50),("goal_getter",c.execute("SELECT count(*) FROM industrial_objective_progress WHERE discord_user_id=? AND completed_at IS NOT NULL",(uid,)).fetchone()[0]>0),("industrial_empire",m["company_value"]>=1000000))
            for slug,earned in rules:
                if earned:
                    tid=c.execute("SELECT id FROM industrial_titles WHERE slug=?",(slug,)).fetchone()[0];fresh=c.execute("INSERT OR IGNORE INTO industrial_user_titles(discord_user_id,title_id,source_reference) VALUES(?,?,?)",(uid,tid,f"progression:{slug}")).rowcount
                    if fresh:c.execute("INSERT OR IGNORE INTO industrial_notification_events(discord_user_id,event_type,event_key,payload) VALUES(?,'title',?,?)",(uid,f"title:{slug}",json.dumps({"slug":slug})))
            return [dict(r) for r in c.execute("SELECT t.id,t.slug,t.display_name,t.description,t.source,t.rarity,ut.unlocked_at,ut.equipped FROM industrial_user_titles ut JOIN industrial_titles t ON t.id=ut.title_id WHERE ut.discord_user_id=? ORDER BY ut.equipped DESC,ut.unlocked_at DESC",(uid,))]

    def equip_title(self,uid,selector,request_id):
        with immediate_transaction(self.database_path) as c:
            self._ensure_user(c,uid);row=c.execute("SELECT ut.title_id,t.slug,t.display_name FROM industrial_user_titles ut JOIN industrial_titles t ON t.id=ut.title_id WHERE ut.discord_user_id=? AND (t.slug=? OR cast(t.id AS TEXT)=?)",(uid,selector,selector)).fetchone()
            if not row:return None
            previous=c.execute("SELECT discord_user_id,action,title_id FROM industrial_title_actions WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if (int(previous[0]),previous[1],int(previous[2]))!=(uid,"equip",int(row["title_id"])):raise ValueError("request id parameter mismatch")
                return dict(row)
            c.execute("UPDATE industrial_user_titles SET equipped=0 WHERE discord_user_id=?",(uid,));c.execute("UPDATE industrial_user_titles SET equipped=1 WHERE discord_user_id=? AND title_id=?",(uid,row["title_id"]));c.execute("INSERT INTO industrial_title_actions(request_id,discord_user_id,action,title_id) VALUES(?,?,'equip',?)",(request_id,uid,row["title_id"]));return dict(row)

    def remove_title(self,uid,request_id):
        with immediate_transaction(self.database_path) as c:
            previous=c.execute("SELECT discord_user_id,action FROM industrial_title_actions WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if (int(previous[0]),previous[1])!=(uid,"remove"):raise ValueError("request id parameter mismatch")
                return
            c.execute("UPDATE industrial_user_titles SET equipped=0 WHERE discord_user_id=?",(uid,));c.execute("INSERT INTO industrial_title_actions(request_id,discord_user_id,action) VALUES(?,?,'remove')",(request_id,uid))

    def _team_membership(self,c,uid,company_id=None):
        sql="SELECT tm.*,co.name FROM industrial_team_members tm JOIN industrial_companies co ON co.id=tm.company_id WHERE tm.discord_user_id=?";args=[uid]
        if company_id is not None:sql+=" AND tm.company_id=?";args.append(company_id)
        sql+=" ORDER BY CASE tm.role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END,tm.joined_at"
        return c.execute(sql,args).fetchone()

    def _team_audit(self,c,company,actor,target,action,request_id,metadata=None):
        previous=c.execute("SELECT company_id,actor_discord_user_id,target_discord_user_id,action FROM industrial_team_audit WHERE request_id=?",(request_id,)).fetchone()
        if previous:
            if (int(previous[0]),int(previous[1]),int(previous[2]) if previous[2] is not None else None,previous[3])!=(company,actor,target,action):raise ValueError("request id parameter mismatch")
            return
        c.execute("INSERT OR IGNORE INTO industrial_team_audit(company_id,actor_discord_user_id,target_discord_user_id,action,metadata,request_id) VALUES(?,?,?,?,?,?)",(company,actor,target,action,json.dumps(metadata or {}),request_id))

    def get_team(self,uid):
        now=_now()
        with self._read() as c:
            memberships=[dict(r) for r in c.execute("SELECT tm.*,co.name FROM industrial_team_members tm JOIN industrial_companies co ON co.id=tm.company_id WHERE tm.discord_user_id=? ORDER BY tm.role",(uid,))]
            invites=[dict(r) for r in c.execute("SELECT i.*,co.name FROM industrial_team_invitations i JOIN industrial_companies co ON co.id=i.company_id WHERE i.invitee_discord_user_id=? AND i.status='pending' AND i.expires_at>? ORDER BY i.created_at",(uid,now))]
            teams=[]
            for member in memberships:
                member["members"]=[dict(r) for r in c.execute("SELECT discord_user_id,role,joined_at FROM industrial_team_members WHERE company_id=? ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END,joined_at",(member["company_id"],))];member["permissions"]=sorted(ROLE_PERMISSIONS[member["role"]]);teams.append(member)
            return {"teams":teams,"invitations":invites}

    def invite_team_member(self,uid,target,request_id):
        if uid==target:raise ValueError("self invitation")
        with immediate_transaction(self.database_path) as c:
            member=self._team_membership(c,uid)
            if not member or "invite" not in ROLE_PERMISSIONS[member["role"]]:return {"status":"forbidden"}
            self._ensure_user(c,target);existing=c.execute("SELECT * FROM industrial_team_invitations WHERE request_id=?",(request_id,)).fetchone()
            if existing:
                if (existing["company_id"],existing["inviter_discord_user_id"],existing["invitee_discord_user_id"])!=(member["company_id"],uid,target):raise ValueError("request id parameter mismatch")
                return {"status":"duplicate","id":int(existing["id"])}
            pending=c.execute("SELECT id FROM industrial_team_invitations WHERE company_id=? AND invitee_discord_user_id=? AND status='pending' AND expires_at>?",(member["company_id"],target,_now())).fetchone()
            if pending:return {"status":"pending","id":int(pending[0])}
            cur=c.execute("INSERT INTO industrial_team_invitations(company_id,inviter_discord_user_id,invitee_discord_user_id,request_id) VALUES(?,?,?,?)",(member["company_id"],uid,target,request_id));self._team_audit(c,member["company_id"],uid,target,"invite",f"audit:{request_id}");c.execute("INSERT OR IGNORE INTO industrial_notification_events(discord_user_id,event_type,event_key,payload) VALUES(?,'team',?,?)",(target,f"team_invite:{cur.lastrowid}",json.dumps({"company_id":member["company_id"],"invitation_id":cur.lastrowid})));return {"status":"pending","id":int(cur.lastrowid)}

    def resolve_team_invitation(self,uid,invitation_id,action,request_id):
        if action not in {"accept","decline"}:raise ValueError("invalid invitation action")
        with immediate_transaction(self.database_path) as c:
            invite=c.execute("SELECT * FROM industrial_team_invitations WHERE id=? AND invitee_discord_user_id=?",(invitation_id,uid)).fetchone()
            if not invite:return {"status":"not_found"}
            if invite["status"]!="pending":return {"status":invite["status"]}
            if int(invite["expires_at"])<=_now():c.execute("UPDATE industrial_team_invitations SET status='expired',resolved_at=? WHERE id=?",(_now(),invitation_id));return {"status":"expired"}
            status="accepted" if action=="accept" else "declined";c.execute("UPDATE industrial_team_invitations SET status=?,resolved_at=? WHERE id=? AND status='pending'",(status,_now(),invitation_id))
            if action=="accept":c.execute("INSERT OR IGNORE INTO industrial_team_members(company_id,discord_user_id,role) VALUES(?,?,'employee')",(invite["company_id"],uid))
            self._team_audit(c,invite["company_id"],uid,uid,status,request_id);return {"status":status}

    def change_team(self,uid,action,target,role,request_id):
        with immediate_transaction(self.database_path) as c:
            actor=self._team_membership(c,uid)
            if not actor:return {"status":"forbidden"}
            if action=="leave":
                employment=c.execute("SELECT tm.*,co.name FROM industrial_team_members tm JOIN industrial_companies co ON co.id=tm.company_id WHERE tm.discord_user_id=? AND tm.role<>'owner' ORDER BY tm.joined_at LIMIT 1",(uid,)).fetchone()
                if employment:actor=employment
                if actor["role"]=="owner":return {"status":"owner_cannot_leave"}
                c.execute("DELETE FROM industrial_team_members WHERE company_id=? AND discord_user_id=?",(actor["company_id"],uid));self._team_audit(c,actor["company_id"],uid,uid,"leave",request_id);return {"status":"left"}
            if actor["role"]!="owner":return {"status":"forbidden"}
            target_member=self._team_membership(c,target,actor["company_id"])
            if not target_member or target_member["role"]=="owner":return {"status":"not_found"}
            if action=="remove":c.execute("DELETE FROM industrial_team_members WHERE company_id=? AND discord_user_id=?",(actor["company_id"],target));status="removed"
            elif action=="role" and role in {"manager","employee"}:c.execute("UPDATE industrial_team_members SET role=?,updated_at=? WHERE company_id=? AND discord_user_id=?",(role,_now(),actor["company_id"],target));status=role
            else:return {"status":"invalid"}
            self._team_audit(c,actor["company_id"],uid,target,f"member_{status}",request_id,{"role":role});return {"status":status}
