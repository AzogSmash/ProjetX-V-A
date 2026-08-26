import re
import time

import discord

from economy_v2.router import EconomyCommandContext

USER = re.compile(r"(?:<@!?(\d+)>|(\d+))\Z")


def _user(token):
    match = USER.fullmatch(token)
    return int(match.group(1) or match.group(2)) if match else None


def _admin(message):
    permissions = getattr(message.author, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def build_fiche_command(service):
    async def command(ctx):
        if len(ctx.args) > 1: return await ctx.message.channel.send("Syntaxe : `?fiche [@utilisateur|id]`.")
        uid = ctx.message.author.id if not ctx.args else _user(ctx.args[0])
        if not uid: return await ctx.message.channel.send("Utilisateur invalide.")
        data = await service.get_industrial_profile(uid)
        if data is None: return await ctx.message.channel.send("Ce joueur n'a pas encore de profil industriel.")
        company=data["company"]; inv=data["inventory"]
        embed=discord.Embed(title="Fiche industrielle",description=f"Utilisateur : <@{uid}>",color=0xD68910)
        embed.add_field(name="Activité",value=f"Métier : **{data['job'] or 'Aucun'}**\nEntreprise : **{company['name'] if company else 'Aucune'}**\nTaille : **{data['company_size']}**",inline=False)
        embed.add_field(name="Progression",value=f"CR : **{data['credits']:,}**\nValeur estimée : **{data['company_value']:,}**\nRéputation : **{data['reputation']:,}**\nClassement fortune : **#{data['money_rank']}**",inline=False)
        embed.add_field(name="Inventaire",value=f"Minerai : **{inv.get('iron_ore',0):,}**\nLingots : **{inv.get('iron_ingot',0):,}**",inline=False)
        embed.add_field(name="Production et échanges",value=f"Minerai produit : **{data['ore_produced']:,}**\nLingots forgés : **{data['ingots_forged']:,}**\nVolume marché : **{data['market_volume']:,} CR**\nTransports : **{data['transports']:,}**\nLivraisons : **{data['deliveries']:,}**\nContrats : **{data['contracts_completed']:,}**",inline=False)
        embed.add_field(name="Titres",value="\n".join(data["achievement_titles"]) or "Aucun",inline=False)
        embed.add_field(name="Titre principal",value=data.get("equipped_title") or "Aucun",inline=False)
        teams=data.get("team_roles",[]);embed.add_field(name="Équipes",value=("\n".join(f"{t['name']} — {t['role']}" for t in teams) or "Aucune")[:1024],inline=False)
        embed.set_footer(text="Détails : ?bilan • progression : ?next")
        await ctx.message.channel.send(embed=embed)
    return command


def build_rank_command(service):
    async def command(ctx):
        category=ctx.args[0].casefold() if ctx.args else "money"
        if len(ctx.args)>1 or category not in {"money","companies","production","market","delivery","contracts"}: return await ctx.message.channel.send("Syntaxe : `?rank [money|companies|production|market|delivery|contracts]`.")
        rows=await service.get_rankings(category); keys={"money":"credits","companies":"company_value","production":"ore_produced","market":"market_volume","delivery":"deliveries","contracts":"contracts_completed"};key=keys[category]
        lines=[f"**{i}.** <@{r['discord_user_id']}> — **{(r['ore_produced']+r['ingots_forged']) if category=='production' else r[key]:,}**" for i,r in enumerate(rows,1)]
        await ctx.message.channel.send(embed=discord.Embed(title=f"Classement industriel — {category}",description="\n".join(lines) or "Aucune donnée.",color=0x3498DB))
    return command


def build_achievements_command(service):
    async def command(ctx):
        if ctx.args:return await ctx.message.channel.send("Syntaxe : `?achievements`.")
        rows=await service.refresh_achievements(ctx.message.author.id)
        text="\n".join(f"🏅 **{r['title']}** (+{r['reputation_awarded']} réputation)" for r in rows) or "Aucun succès débloqué."
        await ctx.message.channel.send(embed=discord.Embed(title="Succès industriels",description=text[:4000],color=0xF1C40F))
    return command


def build_objectives_command(service):
    async def command(ctx):
        if ctx.args:return await ctx.message.channel.send("Syntaxe : `?objectives`.")
        rows=await service.get_objectives(ctx.message.author.id); fields={"daily":[],"weekly":[]}
        for r in rows:fields[r["period"]].append(f"{'✅' if r['completed'] else '⬜'} {r['label']} — {min(r['progress'],r['target']):,}/{r['target']:,}")
        embed=discord.Embed(title="Objectifs industriels",color=0x2ECC71)
        embed.add_field(name="Quotidiens (UTC)",value="\n".join(fields["daily"])[:1024] or "Aucun",inline=False);embed.add_field(name="Hebdomadaires (UTC)",value="\n".join(fields["weekly"])[:1024] or "Aucun",inline=False)
        await ctx.message.channel.send(embed=embed)
    return command


def build_bilan_command(service):
    async def command(ctx):
        if ctx.args:return await ctx.message.channel.send("Syntaxe : `?bilan`.")
        d=await service.get_player_stats(ctx.message.author.id)
        if not d:return await ctx.message.channel.send("Tu n'as pas encore de profil industriel.")
        embed=discord.Embed(title="Bilan industriel",color=0x8E44AD)
        embed.add_field(name="Crédits",value=f"Gagnés : **{d['credits_gained']:,} CR**\nDépensés : **{d['credits_spent']:,} CR**\nSolde : **{d['credits']:,} CR**",inline=False)
        embed.add_field(name="Production",value=f"Minerai produit/vendu/acheté : **{d['ore_produced']:,} / {d['ore_sold']:,} / {d['ore_bought']:,}**\nLingots forgés/vendus : **{d['ingots_forged']:,} / {d['ingots_sold']:,}**",inline=False)
        embed.add_field(name="Activité",value=f"Volume économique : **{d['market_volume']:,} CR**\nTransporté : **{d['transport_volume']:,}**\nLivraisons : **{d['deliveries']:,}**\nContrats : **{d['contracts_completed']:,}**",inline=False)
        embed.add_field(name="Entreprise",value=f"Réputation : **{d['reputation']:,}**\nValeur : **{d['company_value']:,}**\nSuccès : **{d['achievements']:,}**",inline=False)
        await ctx.message.channel.send(embed=embed)
    return command


def build_orders_command(service):
    async def command(ctx):
        if ctx.args:return await ctx.message.channel.send("Syntaxe : `?orders`.")
        d=await service.get_orders_overview(ctx.message.author.id);embed=discord.Embed(title="Mes opérations en cours",color=0x3498DB)
        for title,key in (("Ordres marché","market_orders"),("Contrats","contracts"),("Transports","transports"),("Forge","forge_jobs"),("Expéditions","shipments"),("Missions","missions")):
            rows=d[key];embed.add_field(name=title,value="\n".join(f"`#{r['id']}` — {r.get('status','open')}" for r in rows)[:1024] or "Aucun",inline=False)
        if d["cooldown_until"]>int(time.time()):embed.add_field(name="Cooldown",value=f"Disponible <t:{d['cooldown_until']}:R>",inline=False)
        await ctx.message.channel.send(embed=embed)
    return command


def build_partners_command(service):
    async def command(ctx):
        if not ctx.args:
            rows=await service.get_partnerships(ctx.message.author.id);lines=[]
            for r in rows:lines.append(f"<@{r['high_user_id'] if r['low_user_id']==ctx.message.author.id else r['low_user_id']}> — **{r['status']}**")
            return await ctx.message.channel.send(embed=discord.Embed(title="Partenaires industriels",description=("\n".join(lines) or "Aucun partenariat.")[:4000],color=0x1ABC9C))
        if len(ctx.args)!=2 or ctx.args[0] not in {"add","remove"} or not _user(ctx.args[1]):return await ctx.message.channel.send("Syntaxe : `?partners [add|remove] <@utilisateur|id>`.")
        result=await service.update_partnership(ctx.message.author.id,_user(ctx.args[1]),ctx.args[0],f"discord:{ctx.message.id}")
        messages={"pending":"Invitation envoyée.","accepted":"Partenariat accepté.","removed":"Partenariat retiré.","unknown_target":"Profil industriel inconnu.","incompatible":"Métiers incompatibles pour ce partenariat.","not_found":"Partenariat introuvable."}
        await ctx.message.channel.send(messages[result["status"]])
    return command


def build_notifications_command(service):
    async def command(ctx):
        if not ctx.args:
            d=await service.get_notification_preferences(ctx.message.author.id);return await ctx.message.channel.send("Notifications : " + ("activées" if d["enabled"] else "désactivées") + ".")
        category="all" if len(ctx.args)==1 else ctx.args[0].casefold();state=ctx.args[-1].casefold()
        if state not in {"on","off"} or category not in {"all","market","transport","forge","shipment","contract","season","event","team"}:return await ctx.message.channel.send("Syntaxe : `?notifications [market|transport|forge|shipment|contract|season|event|team] on|off`.")
        await service.set_notification_preference(ctx.message.author.id,category,state=="on");await ctx.message.channel.send("✅ Préférence enregistrée.")
    return command


def build_adminlog_command(service):
    async def command(ctx):
        if not _admin(ctx.message):return await ctx.message.channel.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        if len(ctx.args)!=1 or not _user(ctx.args[0]):return await ctx.message.channel.send("Syntaxe : `?adminlog <@utilisateur|id>`.")
        rows=await service.get_admin_log(_user(ctx.args[0]));lines=[f"<t:{r['created_at']}:d> `{r['transaction_type']}` — {r['credits'] or 0:,} CR" for r in rows]
        await ctx.message.channel.send(embed=discord.Embed(title="Journal industriel",description="\n".join(lines)[:4000] or "Aucune opération.",color=0xE67E22))
    return command


def build_economycheck_command(service):
    async def command(ctx):
        if not _admin(ctx.message):return await ctx.message.channel.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        if ctx.args:return await ctx.message.channel.send("Syntaxe : `?economycheck`.")
        data=await service.economy_check();lines=[f"{'✅' if count==0 else '❌'} {name}" + (f" — {count} problème(s)" if count else "") for name,count in data["checks"].items()]
        await ctx.message.channel.send(embed=discord.Embed(title="Diagnostic économie industrielle",description="\n".join(lines),color=0x2ECC71 if all(v==0 for v in data['checks'].values()) else 0xE74C3C))
    return command
