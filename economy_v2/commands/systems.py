import re
import time

import discord

from economy_v2.router import EconomyCommandContext

USER=re.compile(r"(?:<@!?(\d+)>|(\d+))\Z")
def _uid(value):
    match=USER.fullmatch(value);return int(match.group(1) or match.group(2)) if match else None


def build_season_command(service):
    async def command(ctx: EconomyCommandContext):
        action=ctx.args[0].casefold() if ctx.args else "overall"
        if action=="rank":action="overall"
        if action=="history" and len(ctx.args)==1:
            rows=await service.get_season_history();text="\n".join(f"**S{r['season_number']}** — {r['name']}" for r in rows) or "Aucune saison terminée."
            return await ctx.message.channel.send(embed=discord.Embed(title="Historique des saisons",description=text[:4000],color=0x9B59B6))
        if len(ctx.args)>1 or action not in {"overall","mine","merchant","forge","bank","delivery","contracts"}:return await ctx.message.channel.send("Syntaxe : `?season [rank|mine|merchant|forge|bank|delivery|contracts|history]`.")
        data=await service.get_season_dashboard(ctx.message.author.id,action);season=data["season"];remaining=max(0,int(season["ends_at"])-int(time.time()))
        lines=[f"**{i}.** <@{r['discord_user_id']}> — **{r['score']:,} pts**" for i,r in enumerate(data["ranking"],1)]
        embed=discord.Embed(title=f"🏆 {season['name']}",description=f"Début : <t:{season['starts_at']}:D>\nFin : <t:{season['ends_at']}:R>\nDurée restante : **{remaining//86400}j {(remaining%86400)//3600}h**",color=0x9B59B6)
        embed.add_field(name="Ton parcours",value=f"Score {action} : **{data['scores'][action]:,}**\nRang : **#{data['rank']}**",inline=False);embed.add_field(name=f"Top {action}",value=("\n".join(lines) or "Aucun score.")[:1024],inline=False);embed.set_footer(text="Récompenses cosmétiques uniquement • aucun reset économique")
        await ctx.message.channel.send(embed=embed)
    return command


def build_titles_command(service):
    async def command(ctx):
        if ctx.args:return await ctx.message.channel.send("Syntaxe : `?titles`.")
        rows=await service.refresh_titles(ctx.message.author.id);lines=[f"`{r['slug']}` — **{r['display_name']}** [{r['rarity']}] {'• équipé' if r['equipped'] else ''}" for r in rows]
        await ctx.message.channel.send(embed=discord.Embed(title="Titres cosmétiques",description=("\n".join(lines) or "Aucun titre débloqué.")[:4000],color=0xF1C40F))
    return command


def build_title_command(service):
    async def command(ctx):
        if len(ctx.args)==2 and ctx.args[0].casefold() in {"equip","equiper","équiper"}:
            row=await service.equip_title(ctx.message.author.id,ctx.args[1],f"discord:{ctx.message.id}");return await ctx.message.channel.send(f"✅ Titre équipé : **{row['display_name']}**" if row else "Titre non débloqué.")
        if len(ctx.args)==1 and ctx.args[0].casefold() in {"remove","retirer"}:
            await service.remove_title(ctx.message.author.id,f"discord:{ctx.message.id}");return await ctx.message.channel.send("✅ Titre principal retiré.")
        await ctx.message.channel.send("Syntaxe : `?title equip <id|slug>` ou `?title remove`.")
    return command


def build_events_command(service):
    async def command(ctx):
        if ctx.args:return await ctx.message.channel.send("Syntaxe : `?events`.")
        rows=await service.get_active_events()
        if not rows:return await ctx.message.channel.send("Aucun événement économique actif.")
        labels={"mining_rush":"Production minerai","industrial_boom":"Vitesse forge","world_demand":"Prix mondial","logistics_rush":"Durée transport","delivery_bonus":"XP livraison"};fields=[]
        for row in rows:
            pct=(row["multiplier_bps"]-10000)/100;fields.append((row["display_name"],f"{labels[row['event_type']]} : **{pct:+.0f} %**\nFin : <t:{row['ends_at']}:R>"))
        embed=discord.Embed(title="🌐 Événements économiques actuels",color=0xE67E22)
        for name,value in fields[:25]:embed.add_field(name=name,value=value[:1024],inline=False)
        await ctx.message.channel.send(embed=embed)
    return command


def build_team_command(service):
    async def command(ctx):
        uid=ctx.message.author.id
        if not ctx.args:
            data=await service.get_team(uid);lines=[]
            for team in data["teams"]:
                lines.append(f"**{team['name']}** — rôle **{team['role']}**")
                lines.extend(f"• <@{m['discord_user_id']}> — {m['role']}" for m in team["members"][:15])
            invites=[f"`#{i['id']}` {i['name']} — expire <t:{i['expires_at']}:R>" for i in data["invitations"]]
            embed=discord.Embed(title="Équipe d’entreprise",description=("\n".join(lines) or "Aucune équipe.")[:4000],color=0x1ABC9C);embed.add_field(name="Invitations",value=("\n".join(invites) or "Aucune")[:1024],inline=False);return await ctx.message.channel.send(embed=embed)
        action=ctx.args[0].casefold();request=f"discord:{ctx.message.id}"
        if action=="invite" and len(ctx.args)==2 and _uid(ctx.args[1]):result=await service.invite_team_member(uid,_uid(ctx.args[1]),request)
        elif action in {"accept","decline"} and len(ctx.args)==2 and ctx.args[1].isdigit():result=await service.resolve_team_invitation(uid,int(ctx.args[1]),action,request)
        elif action=="remove" and len(ctx.args)==2 and _uid(ctx.args[1]):result=await service.change_team(uid,"remove",_uid(ctx.args[1]),None,request)
        elif action=="leave" and len(ctx.args)==1:result=await service.change_team(uid,"leave",None,None,request)
        elif action=="role" and len(ctx.args)==3 and _uid(ctx.args[1]) and ctx.args[2] in {"manager","employee"}:result=await service.change_team(uid,"role",_uid(ctx.args[1]),ctx.args[2],request)
        else:return await ctx.message.channel.send("Syntaxe : `?equipe [invite <user>|accept <id>|decline <id>|remove <user>|leave|role <user> <manager|employee>]`.")
        messages={"pending":"Invitation envoyée.","duplicate":"Invitation déjà enregistrée.","accepted":"Invitation acceptée.","declined":"Invitation refusée.","expired":"Invitation expirée.","removed":"Membre retiré.","left":"Tu as quitté l’équipe.","manager":"Rôle manager attribué.","employee":"Rôle employee attribué.","forbidden":"Permission insuffisante.","owner_cannot_leave":"Le propriétaire ne peut pas quitter son entreprise.","not_found":"Invitation ou membre introuvable.","invalid":"Action invalide."};await ctx.message.channel.send(messages.get(result["status"],"Action impossible."))
    return command
