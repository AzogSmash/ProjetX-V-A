from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord

from economy_v2.router import EconomyCommandContext

logger = logging.getLogger(__name__)


def _n(value, decimals=0):
    if value is None:return "N/A"
    return f"{value:,.{decimals}f}" if isinstance(value,float) else f"{value:,}"


def _pct(value):return f"{value:.1f} %"
def _avg(block):return "N/A" if block["average"] is None else f"{block['average']:.2f} CR"


def build_report_embeds(r):
    g,w,j,p=r["global"],r["wealth"],r["jobs"],r["production"]
    embeds=[];e=discord.Embed(title="Rapport d’équilibrage industriel",description=f"Généré <t:{r['generated_at']}:F> — données SQLite en lecture seule",color=0x5865F2)
    e.add_field(name="Économie globale",value=f"Joueurs : **{g['players']:,}** • Entreprises : **{g['companies']:,}**\nCR joueurs : **{g['player_cr']:,}** • IA : **{g['ai_cr']:,}**\n24 h : +{g['day']['created']:,} / -{g['day']['destroyed']:,} CR\n7 j : +{g['week']['created']:,} / -{g['week']['destroyed']:,} CR\nNet 7 j : **{g['week']['created']-g['week']['destroyed']:+,} CR**",inline=False)
    created=max(1,g["week"]["created"]);e.add_field(name="Sources CR — 7 jours",value=f"Admin : **{_pct(100*g['week']['admin']/created)}**\nIA : **{_pct(100*g['week']['ai']/created)}**\nMarché mondial : **{_pct(100*g['week']['world']/created)}**",inline=True)
    e.add_field(name="Richesse",value=f"Moyenne : **{w['average']:,.1f} CR**\nMédiane : **{w['median']:,.1f} CR**\nTop 10 % : **{_pct(w['top_decile_share'])}**\nMaximum : **{w['richest']:,} CR**\nÉcart max/médiane : **{w['median_top_gap']:,.1f} CR**",inline=True)
    e.add_field(name="Métiers joueurs",value="\n".join(f"{x} : **{j['players'].get(k,0):,}**" for k,x in (("miner","Mineurs"),("merchant","Marchands"),("blacksmith","Forgerons"),("banker","Banquiers")))+f"\nRatio joueurs/IA : **{j['ratio']:.2f}**" if j['ratio'] is not None else "Aucun acteur IA.",inline=True)
    e.add_field(name="Métiers IA",value="\n".join(f"{k} : **{v:,}**" for k,v in sorted(j["ai"].items())) or "Aucun",inline=True)
    e.add_field(name="Production",value=f"Minerai 24 h / 7 j : **{p['day']['ore']['total']:,} / {p['week']['ore']['total']:,}**\nLingots 24 h / 7 j : **{p['day']['ingots']['total']:,} / {p['week']['ingots']['total']:,}**\nPart IA minerai : **{_pct(100*p['week']['ore']['ai']/max(1,p['week']['ore']['total']))}**\nPart IA lingots : **{_pct(100*p['week']['ingots']['ai']/max(1,p['week']['ingots']['total']))}**\nMoyenne/joueur actif : **{p['average_per_active']:.1f}**",inline=False);embeds.append(e)

    m,b=r["market"],r["world"];e=discord.Embed(title="Marchés",color=0x3498DB)
    e.add_field(name="Marché iron_ore",value=f"Meilleur achat : **{_n(m['best_buy'])}** • vente : **{_n(m['best_sell'])}**\nSpread : **{_n(m['spread'])} CR**\nPrix moyen 24 h / 7 j : **{_avg(m['day'])} / {_avg(m['week'])}**\nVolume 24 h / 7 j : **{m['day']['volume']:,} / {m['week']['volume']:,}**\nOrdres ouverts : **{m['open_orders']:,}** • valeur : **{m['open_value']:,} CR**\nPart IA volume 7 j : **{_pct(100*m['week']['ai_volume']/max(1,m['week']['volume']))}**",inline=False)
    e.add_field(name="Banque / marché mondial",value=f"Prix actuel : **{b['current_price']:,} CR/lingot**\nMoyenne 24 h / 7 j : **{_avg(b['day'])} / {_avg(b['week'])}**\nVolume 24 h / 7 j : **{b['day']['volume']:,} / {b['week']['volume']:,}**\nCR créés 24 h / 7 j : **{b['day']['credits']:,} / {b['week']['credits']:,}**",inline=False);embeds.append(e)

    l,d,c=r["logistics"],r["delivery"],r["contracts"];e=discord.Embed(title="Logistique et échanges directs",color=0x1ABC9C)
    e.add_field(name="Transports",value=f"Créés 24 h / 7 j : **{l['day']['created']:,} / {l['week']['created']:,}**\nTerminés 7 j : **{l['week']['completed']:,}** • actifs : **{l['active']:,}**\nDurée moyenne 7 j : **{l['week']['average_seconds']/60:.1f} min**\nPart IA : **{_pct(100*l['week']['ai']/max(1,l['week']['created']))}**\nCamions distincts / moyenne opérateur : **{l['week']['used_trucks']:,} / {l['week']['average_used_trucks']:.1f}**",inline=False)
    e.add_field(name="Livraisons",value=f"Disponibles : **{d['available']:,}**\nTerminées 24 h / 7 j : **{d['day']['completed']:,} / {d['week']['completed']:,}**\nTemps économisé 7 j : **{d['week']['saved_seconds']/60:.1f} min**\nCommissions 7 j : **{d['week']['commission']:,} CR** • moyenne : **{d['week']['average_commission']:.1f}**\nLivreurs actifs : **{d['week']['couriers']:,}** • niveau moyen : **{d['average_level']:.1f}**",inline=False)
    e.add_field(name="Contrats",value=f"Ouverts : **{c['open']:,}** • escrow : **{c['escrow']:,} CR**\nCréés 24 h / 7 j : **{c['day']['created']:,} / {c['week']['created']:,}**\nComplétés 24 h / 7 j : **{c['day']['completed']:,} / {c['week']['completed']:,}**\nExpirés 7 j : **{c['week']['expired']:,}**\nValeur terminée 7 j : **{c['week']['completed_value']:,} CR**",inline=False);embeds.append(e)

    x,s,events=r["progression"],r["season"],r["events"];e=discord.Embed(title="Progression et signaux",color=0xF1C40F)
    e.add_field(name="Entreprises / progression",value=f"Valeur comptable moyenne : **{x['company_value_average']:,.1f}**\nRéputation persistante moyenne : **{x['reputation_average']:,.1f}**\nAchievements 24 h / 7 j : **{x['achievements_24h']:,} / {x['achievements_7d']:,}**\nObjectifs 24 h / 7 j : **{x['objectives_24h']:,} / {x['objectives_7d']:,}**",inline=False)
    infra=[]
    for job,levels in x["infrastructure"].items():infra.append(f"{job} : "+", ".join(f"{k} {v:.1f}" for k,v in levels.items()))
    e.add_field(name="Infrastructures moyennes",value=("\n".join(infra) or "N/A")[:1024],inline=False)
    e.add_field(name="Saison",value=(f"**{s['name']}** — fin <t:{s['ends_at']}:R>\nParticipants : **{s['participants']:,}** • top : **{s['top_score']:,}**\nScores min/moy/max : **{s['minimum_score']:,} / {s['average_score']:.1f} / {s['top_score']:,}**\nCatégories : "+(", ".join(f"{q['category']} {q['score']:,}" for q in s['categories'][:6]) or "N/A")) if s else "Aucune saison active.",inline=False)
    event_lines=[f"**{q['display_name']}** — x{q['multiplier_bps']/10000:.2f}, fin <t:{q['ends_at']}:R>\nObservé 24 h : {q['observed_24h']:,.1f} • écart dérivé : {q['estimated_delta_24h']:+,.1f}" for q in events]
    e.add_field(name="Événements actifs",value="\n".join(event_lines)[:1024] or "Aucun événement économique actif.",inline=False)
    e.add_field(name="Alertes d’équilibrage",value=("\n".join("⚠️ "+a for a in r["alerts"]) or "✅ Aucun drapeau automatique.")[:1024],inline=False);embeds.append(e)
    return embeds


def build_report_text(r):
    g,j,p,m,b,l,d,c,w,x,s=r["global"],r["jobs"],r["production"],r["market"],r["world"],r["logistics"],r["delivery"],r["contracts"],r["wealth"],r["progression"],r["season"]
    stamp=datetime.fromtimestamp(r["generated_at"],timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines=["ECONOMY REPORT",f"Generated: {stamp}","", "MONEY",f"Players/companies: {g['players']}/{g['companies']}",f"Players CR: {g['player_cr']} | AI CR: {g['ai_cr']}",f"Created 24h/7d: {g['day']['created']}/{g['week']['created']}",f"Destroyed 24h/7d: {g['day']['destroyed']}/{g['week']['destroyed']}",f"Net 7d: {g['week']['created']-g['week']['destroyed']}",f"Sources 7d admin/AI/world: {g['week']['admin']}/{g['week']['ai']}/{g['week']['world']}","","JOBS",f"Players: {j['players']}",f"AI: {j['ai']}","","PRODUCTION",f"Ore 24h/7d: {p['day']['ore']['total']}/{p['week']['ore']['total']} (AI {p['week']['ore']['ai']})",f"Ingots 24h/7d: {p['day']['ingots']['total']}/{p['week']['ingots']['total']} (AI {p['week']['ingots']['ai']})",f"Average per active player: {p['average_per_active']:.2f}","","MARKET",f"Best buy/sell/spread: {m['best_buy']}/{m['best_sell']}/{m['spread']}",f"Average 24h/7d: {m['day']['average']}/{m['week']['average']}",f"Volume 24h/7d: {m['day']['volume']}/{m['week']['volume']}",f"Open orders/value: {m['open_orders']}/{m['open_value']}","","WORLD MARKET",f"Current price: {b['current_price']}",f"Average 24h/7d: {b['day']['average']}/{b['week']['average']}",f"Volume 24h/7d: {b['day']['volume']}/{b['week']['volume']}",f"Created CR 24h/7d: {b['day']['credits']}/{b['week']['credits']}","","LOGISTICS",f"Transports 24h/7d/active: {l['day']['created']}/{l['week']['created']}/{l['active']}",f"Deliveries 24h/7d: {d['day']['completed']}/{d['week']['completed']}",f"Contracts open/completed7d/expired7d/escrow: {c['open']}/{c['week']['completed']}/{c['week']['expired']}/{c['escrow']}","","WEALTH",f"Average/median/richest: {w['average']:.2f}/{w['median']:.2f}/{w['richest']}",f"Top 10% share: {w['top_decile_share']:.2f}%", "","PROGRESSION",f"Company value avg: {x['company_value_average']:.2f}",f"Reputation avg: {x['reputation_average']:.2f}",f"Achievements 24h/7d: {x['achievements_24h']}/{x['achievements_7d']}",f"Objectives 24h/7d: {x['objectives_24h']}/{x['objectives_7d']}","","SEASON",("None" if not s else f"{s['name']} | participants {s['participants']} | top {s['top_score']}"),"","EVENTS"]
    lines.extend(f"{q['event_type']}: x{q['multiplier_bps']/10000:.2f} until {q['ends_at']}" for q in r["events"]);lines.extend(["","ALERTS"]);lines.extend(r["alerts"] or ["None"]);return "\n".join(lines)


def build_economy_report_command(service):
    async def command(ctx: EconomyCommandContext):
        permissions=getattr(ctx.message.author,"guild_permissions",None)
        if not permissions or not permissions.administrator:return await ctx.message.channel.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        if len(ctx.args)>1 or (ctx.args and ctx.args[0].casefold()!="text"):return await ctx.message.channel.send("Syntaxe : `?economyreport [text]`.")
        report=await service.get_economy_report();logger.info("[ECONOMY REPORT] admin=%s generated_at=%s",ctx.message.author.id,report["generated_at"])
        if ctx.args:
            text=build_report_text(report)
            for start in range(0,len(text),1900):await ctx.message.channel.send("```text\n"+text[start:start+1900]+"\n```")
            return
        for embed in build_report_embeds(report):await ctx.message.channel.send(embed=embed)
    return command
