from __future__ import annotations

import time
from math import ceil

from economy_v2.progression import company_value
from economy_v2.mining_config import get_storage_capacity
from economy_v2.world_market_config import bounded_world_price


BALANCE_ALERT_THRESHOLDS = {
    "ai_production_percent": 40.0,
    "job_share_percent": 10.0,
    "weekly_money_growth_percent": 25.0,
    "market_spread_percent": 50.0,
    "top_decile_wealth_percent": 70.0,
    "expired_contract_percent": 35.0,
    "minimum_weekly_deliveries": 3,
    "blocked_mine_percent": 60.0,
}


def _percent(part: int | float, total: int | float) -> float:
    return 100.0 * part / total if total else 0.0


class IndustrialReportingMixin:
    """Read-only server-wide aggregates used by the administrative report."""

    def get_economy_report(self, now: int | None = None) -> dict:
        now = int(now or time.time()); day = now - 86400; week = now - 604800
        with self._read() as c:
            scalar = lambda sql, args=(): c.execute(sql, args).fetchone()[0]
            wallet_rows=[(int(r[0]),int(r[1])) for r in c.execute("SELECT discord_user_id,credits FROM industrial_users ORDER BY credits")];wallets=[r[1] for r in wallet_rows]
            players = len(wallets); total_player_cr = sum(wallets)
            median = 0.0 if not wallets else (float(wallets[players//2]) if players % 2 else (wallets[players//2-1]+wallets[players//2])/2)
            decile_count = ceil(players * .1) if players else 0
            top_decile = sum(wallets[-decile_count:]) if decile_count else 0

            def money(since):
                row=c.execute("SELECT coalesce(sum(CASE WHEN monetary_effect='source' THEN credits ELSE 0 END),0),coalesce(sum(CASE WHEN monetary_effect='sink' THEN credits ELSE 0 END),0),coalesce(sum(CASE WHEN monetary_effect='source' AND transaction_type LIKE 'admin_credit_%' THEN credits ELSE 0 END),0),coalesce(sum(CASE WHEN monetary_effect='source' AND a.actor_type='ai' THEN credits ELSE 0 END),0),coalesce(sum(CASE WHEN monetary_effect='source' AND transaction_type='world_sale' THEN credits ELSE 0 END),0) FROM industrial_transactions t LEFT JOIN industrial_actors a ON a.id=t.actor_id WHERE t.created_at>=?",(since,)).fetchone()
                return {"created":int(row[0]),"destroyed":int(row[1]),"admin":int(row[2]),"ai":int(row[3]),"world":int(row[4])}
            money24,money7=money(day),money(week)

            jobs={r[0]:int(r[1]) for r in c.execute("SELECT coalesce(primary_job,'none'),count(*) FROM industrial_users GROUP BY primary_job")}
            ai_jobs={r[0]:int(r[1]) for r in c.execute("SELECT job_type,count(*) FROM industrial_ai_companies co JOIN industrial_actors a ON a.ai_company_id=co.id GROUP BY job_type")}

            def production(resource,since):
                row=c.execute("SELECT coalesce(sum(quantity),0),coalesce(sum(CASE WHEN actor_type='ai' THEN quantity ELSE 0 END),0),coalesce(sum(CASE WHEN actor_type='player' THEN quantity ELSE 0 END),0) FROM industrial_resource_events WHERE resource_type=? AND created_at>=?",(resource,since)).fetchone()
                return {"total":int(row[0]),"ai":int(row[1]),"player":int(row[2])}
            production24={"ore":production("iron_ore",day),"ingots":production("iron_ingot",day)}
            production7={"ore":production("iron_ore",week),"ingots":production("iron_ingot",week)}
            active_players=int(scalar("SELECT count(*) FROM industrial_user_activity WHERE last_active_at>=?",(week,)))

            def trade(since):
                row=c.execute("SELECT coalesce(sum(total_price),0),coalesce(sum(quantity),0),count(*),coalesce(sum(CASE WHEN sa.actor_type='ai' OR ba.actor_type='ai' THEN quantity ELSE 0 END),0) FROM industrial_market_trades mt JOIN industrial_actors sa ON sa.id=mt.seller_actor_id JOIN industrial_actors ba ON ba.id=mt.buyer_actor_id WHERE mt.resource_type='iron_ore' AND mt.created_at>=?",(since,)).fetchone()
                return {"value":int(row[0]),"volume":int(row[1]),"trades":int(row[2]),"ai_volume":int(row[3]),"average":float(row[0]/row[1]) if row[1] else None}
            trade24,trade7=trade(day),trade(week)
            book=c.execute("SELECT max(CASE WHEN side='buy' THEN unit_price END),min(CASE WHEN side='sell' THEN unit_price END),count(*),coalesce(sum(remaining_quantity*unit_price),0) FROM industrial_market_orders WHERE resource_type='iron_ore' AND status='open'").fetchone()
            best_buy,best_sell=(int(book[0]) if book[0] is not None else None),(int(book[1]) if book[1] is not None else None)

            def world(since):
                row=c.execute("SELECT coalesce(sum(quantity),0),coalesce(sum(total_credits),0) FROM industrial_world_sales WHERE created_at>=?",(since,)).fetchone()
                return {"volume":int(row[0]),"credits":int(row[1]),"average":float(row[1]/row[0]) if row[0] else None}
            world24,world7=world(day),world(week)

            def transports(since):
                row=c.execute("SELECT count(*),coalesce(sum(CASE WHEN status='delivered' THEN 1 ELSE 0 END),0),coalesce(avg(current_duration_seconds),0),coalesce(sum(CASE WHEN a.actor_type='ai' THEN 1 ELSE 0 END),0),count(DISTINCT CASE WHEN status='in_transit' THEN operator_actor_id||':'||truck_slot END) FROM industrial_transports t JOIN industrial_actors a ON a.id=t.operator_actor_id WHERE t.created_at>=?",(since,)).fetchone()
                operators=int(scalar("SELECT count(DISTINCT operator_actor_id) FROM industrial_transports WHERE created_at>=?",(since,)))
                return {"created":int(row[0]),"completed":int(row[1]),"average_seconds":float(row[2]),"ai":int(row[3]),"used_trucks":int(row[4]),"average_used_trucks":float(row[4]/operators) if operators else 0.0}
            transport24,transport7=transports(day),transports(week)
            active_transports=int(scalar("SELECT count(*) FROM industrial_transports WHERE status='in_transit' AND arrival_at>?",(now,)))

            def deliveries(since):
                row=c.execute("SELECT count(*),coalesce(sum(saved_seconds),0),coalesce(sum(commission_paid),0),count(DISTINCT courier_discord_user_id) FROM industrial_delivery_missions WHERE status='accepted' AND accepted_at>=?",(since,)).fetchone()
                return {"completed":int(row[0]),"saved_seconds":int(row[1]),"commission":int(row[2]),"couriers":int(row[3]),"average_commission":float(row[2]/row[0]) if row[0] else 0.0}
            delivery24,delivery7=deliveries(day),deliveries(week)
            available_missions=int(scalar("SELECT count(*) FROM industrial_delivery_missions m JOIN industrial_transports t ON t.id=m.transport_id WHERE m.status='open' AND t.status='in_transit' AND t.arrival_at>?",(now,)))
            avg_delivery_level=float(scalar("SELECT coalesce(avg(delivery_level),0) FROM industrial_delivery_profiles"))

            def contracts(since):
                row=c.execute("SELECT count(*),coalesce(sum(CASE WHEN status='completed' THEN 1 ELSE 0 END),0),coalesce(sum(CASE WHEN status='expired' THEN 1 ELSE 0 END),0),coalesce(sum(CASE WHEN status='completed' THEN total_price ELSE 0 END),0) FROM industrial_contracts WHERE created_at>=?",(since,)).fetchone()
                return {"created":int(row[0]),"completed":int(row[1]),"expired":int(row[2]),"completed_value":int(row[3])}
            contract24,contract7=contracts(day),contracts(week)
            open_contracts=int(scalar("SELECT count(*) FROM industrial_contracts WHERE status='open' AND expires_at>?",(now,)))
            contract_escrow=int(scalar("SELECT coalesce(sum(escrow_credits),0) FROM industrial_contracts WHERE status='open'"))

            user_metrics={uid:{"credits":credits,"inventory":{}} for uid,credits in wallet_rows}
            for r in c.execute("SELECT owner_discord_user_id,resource_type,quantity FROM industrial_inventory WHERE owner_discord_user_id IS NOT NULL"):user_metrics[int(r[0])]["inventory"][r[1]]=int(r[2])
            infra={}
            for job,table,columns in (("miner","industrial_mines",("storage_level","production_level","quality_level")),("merchant","industrial_merchants",("truck_count","truck_capacity_level","truck_speed_level","warehouse_level")),("blacksmith","industrial_blacksmiths",("forge_level","speed_level","storage_level","yield_level"))):
                row=c.execute("SELECT "+",".join(f"coalesce(avg({x}),0)" for x in columns)+f" FROM {table}").fetchone();infra[job]={key:float(row[i]) for i,key in enumerate(columns)}
                metric_key={"miner":"mine","merchant":"merchant","blacksmith":"forge"}[job]
                for item in c.execute("SELECT owner_discord_user_id,"+",".join(columns)+f" FROM {table}"):
                    user_metrics[int(item[0])][metric_key]={key:int(item[i+1]) for i,key in enumerate(columns)}
            infra["banker"]={"company_level":float(scalar("SELECT coalesce(avg(level),0) FROM industrial_companies WHERE job_type='banker'"))}
            book_value=sum(company_value(metrics) for metrics in user_metrics.values())
            reputation_total=int(scalar("SELECT coalesce(sum(reputation),0) FROM industrial_reputation_events"))
            mines=[(int(r[0]),int(r[1])) for r in c.execute("SELECT stock,storage_level FROM industrial_mines")];blocked_mine_percent=_percent(sum(stock>=get_storage_capacity(level) for stock,level in mines),len(mines))
            progress={"company_value_average":book_value/players if players else 0.0,"reputation_average":reputation_total/players if players else 0.0,"achievements_24h":int(scalar("SELECT count(*) FROM industrial_achievements WHERE earned_at>=?",(day,))),"achievements_7d":int(scalar("SELECT count(*) FROM industrial_achievements WHERE earned_at>=?",(week,))),"objectives_24h":int(scalar("SELECT count(*) FROM industrial_objective_progress WHERE completed_at>=?",(day,))),"objectives_7d":int(scalar("SELECT count(*) FROM industrial_objective_progress WHERE completed_at>=?",(week,))),"infrastructure":infra,"blocked_mine_percent":blocked_mine_percent}

            season_row=c.execute("SELECT * FROM industrial_seasons WHERE status='active' ORDER BY starts_at DESC LIMIT 1").fetchone();season=None
            if season_row:
                scores=c.execute("SELECT count(DISTINCT discord_user_id),coalesce(max(score),0),coalesce(avg(score),0),coalesce(min(score),0) FROM industrial_season_scores WHERE season_id=? AND category='overall' AND score>0",(season_row["id"],)).fetchone()
                categories=[{"category":r[0],"score":int(r[1])} for r in c.execute("SELECT category,coalesce(sum(score),0) total FROM industrial_season_scores WHERE season_id=? AND category<>'overall' GROUP BY category ORDER BY total DESC",(season_row["id"],))]
                season={"name":season_row["name"],"starts_at":int(season_row["starts_at"]),"ends_at":int(season_row["ends_at"]),"remaining_seconds":max(0,int(season_row["ends_at"])-now),"participants":int(scores[0]),"top_score":int(scores[1]),"average_score":float(scores[2]),"minimum_score":int(scores[3]),"categories":categories}
            events=[dict(r) for r in c.execute("SELECT event_type,display_name,multiplier_bps,starts_at,ends_at FROM industrial_economic_events WHERE starts_at<=? AND ends_at>? ORDER BY ends_at",(now,now))]
            observed={"mining_rush":production24["ore"]["total"],"industrial_boom":production24["ingots"]["total"],"world_demand":world24["credits"],"logistics_rush":transport24["average_seconds"],"delivery_bonus":int(scalar("SELECT coalesce(sum(xp_awarded),0) FROM industrial_delivery_missions WHERE accepted_at>=?",(day,)))}
            for event in events:
                value=observed.get(event["event_type"],0);bps=int(event["multiplier_bps"]);event["observed_24h"]=value;event["estimated_delta_24h"]=float(value-value*10000/bps) if bps else 0.0

            total_ai_cr=int(scalar("SELECT coalesce(sum(credits),0) FROM industrial_ai_accounts"));companies=int(scalar("SELECT count(*) FROM industrial_companies"))
            market={"best_buy":best_buy,"best_sell":best_sell,"spread":best_sell-best_buy if best_buy is not None and best_sell is not None else None,"open_orders":int(book[2]),"open_value":int(book[3]),"day":trade24,"week":trade7}
            report={"generated_at":now,"global":{"players":players,"companies":companies,"player_cr":total_player_cr,"ai_cr":total_ai_cr,"day":money24,"week":money7},"jobs":{"players":jobs,"ai":ai_jobs,"ratio":players/sum(ai_jobs.values()) if ai_jobs else None},"production":{"day":production24,"week":production7,"active_players":active_players,"average_per_active":sum(x["player"] for x in production7.values())/active_players if active_players else 0.0},"market":market,"world":{"current_price":bounded_world_price(world24["volume"]),"day":world24,"week":world7},"logistics":{"day":transport24,"week":transport7,"active":active_transports},"delivery":{"available":available_missions,"day":delivery24,"week":delivery7,"average_level":avg_delivery_level},"contracts":{"open":open_contracts,"escrow":contract_escrow,"day":contract24,"week":contract7},"wealth":{"median":median,"average":total_player_cr/players if players else 0.0,"top_decile_share":_percent(top_decile,total_player_cr),"richest":wallets[-1] if wallets else 0,"median_top_gap":(wallets[-1]-median) if wallets else 0},"progression":progress,"season":season,"events":events}
            report["alerts"]=self._economy_report_alerts(report)
            return report

    @staticmethod
    def _economy_report_alerts(r):
        t=BALANCE_ALERT_THRESHOLDS;alerts=[];prod=sum(x["total"] for x in r["production"]["week"].values());ai=sum(x["ai"] for x in r["production"]["week"].values())
        if _percent(ai,prod)>t["ai_production_percent"]:alerts.append("IA > 40 % de la production sur 7 jours")
        employed=sum(r["jobs"]["players"].get(j,0) for j in ("miner","merchant","blacksmith","banker"))
        for job in ("miner","merchant","blacksmith","banker"):
            if employed and _percent(r["jobs"]["players"].get(job,0),employed)<t["job_share_percent"]:alerts.append(f"Métier {job} sous-représenté (< 10 %)")
        base=max(1,r["global"]["player_cr"]-r["global"]["week"]["created"]+r["global"]["week"]["destroyed"]);growth=_percent(r["global"]["week"]["created"]-r["global"]["week"]["destroyed"],base)
        if growth>t["weekly_money_growth_percent"]:alerts.append("Croissance nette des CR sur 7 jours > 25 %")
        m=r["market"]
        if m["spread"] is not None and m["best_buy"] and _percent(m["spread"],m["best_buy"])>t["market_spread_percent"]:alerts.append("Spread iron_ore > 50 %")
        if r["wealth"]["top_decile_share"]>t["top_decile_wealth_percent"]:alerts.append("Top 10 % > 70 % de la richesse")
        cw=r["contracts"]["week"]
        if cw["created"] and _percent(cw["expired"],cw["created"])>t["expired_contract_percent"]:alerts.append("Contrats expirés > 35 % sur 7 jours")
        if r["global"]["players"]>=3 and r["delivery"]["week"]["completed"]<t["minimum_weekly_deliveries"]:alerts.append("Moins de 3 livraisons sur 7 jours")
        if r["progression"]["blocked_mine_percent"]>t["blocked_mine_percent"]:alerts.append("Plus de 60 % des mines sont pleines")
        return alerts
