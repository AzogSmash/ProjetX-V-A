import discord

from economy_v2.router import EconomyCommandContext


ALIASES={"suivant":"next","statut":"status","recommencer":"restart"}


def _tutorial_embed(data):
    if data["status"]=="completed":
        embed=discord.Embed(title="🎓 TUTORIEL TERMINÉ",description="Tu connais maintenant les bases de ton métier.",color=0x2ECC71)
        embed.add_field(name="Pour continuer",value="`?next` — prochaine action\n`?ecohelp` — toutes les commandes\n`?fiche` — suivre ton entreprise",inline=False);return embed
    if data["status"]=="stopped":return discord.Embed(title="⏸️ Tutoriel arrêté",description="Reprends avec `?tutorial start` ou recommence avec `?tutorial restart`.",color=0x95A5A6)
    step=data["step"];embed=discord.Embed(title="🎓 TUTORIEL INDUSTRIEL",description=f"Étape **{data['current_step']+1}/{data['total_steps']}** — **{step.title}**\nParcours : **{data['path']}**",color=0x3498DB)
    embed.add_field(name="🎯 Objectif",value=step.objective,inline=False);embed.add_field(name="⌨️ Commande",value=f"`{step.command}`",inline=False);embed.add_field(name="💡 Conseil",value=step.tip,inline=False)
    if step.check:embed.add_field(name="📊 Progression",value=f"{'✅ 1/1' if step.slug in data['completed_checks'] else '⬜ 0/1'} — vérifiée depuis SQLite",inline=False)
    embed.set_footer(text="?tutorial next • ?tutorial status • ?tutorial stop");return embed


def build_tutorial_command(service):
    async def command(ctx:EconomyCommandContext):
        if len(ctx.args)>1:return await ctx.message.channel.send("Syntaxe : `?tutorial [start|next|status|restart|stop]`.")
        action=ALIASES.get(ctx.args[0].casefold(),ctx.args[0].casefold()) if ctx.args else "status"
        if action not in {"start","next","status","restart","stop"}:return await ctx.message.channel.send("Syntaxe : `?tutorial [start|next|status|restart|stop]`.")
        if action=="status":data=await service.get_tutorial(ctx.message.author.id)
        else:data=await service.update_tutorial(ctx.message.author.id,action,f"discord:{ctx.message.id}")
        if data.get("blocked"):await ctx.message.channel.send(f"❌ Cette étape n'est pas encore terminée.\nAction nécessaire : `{data['step'].command}`")
        elif data["status"]=="not_started" and action=="next":await ctx.message.channel.send("Commence avec `?tutorial start`.")
        await ctx.message.channel.send(embed=_tutorial_embed(data))
    return command
