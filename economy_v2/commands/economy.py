import logging
import discord
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import IndustrialEconomyError, IndustrialEconomyService

logger = logging.getLogger(__name__)

def build_economy_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def economy_command(context: EconomyCommandContext) -> None:
        if context.args:
            await context.message.channel.send("Syntaxe : `?economy`."); return
        try:
            await service.evaluate_ai_companies()
            s = await service.get_economy_stats()
            embed = discord.Embed(title="📊 Économie industrielle — 24 h",color=0x34495E)
            player_credits, ai_credits = int(s["player_credits"]), int(s["ai_credits"])
            embed.add_field(name="CR en circulation",value=f"{player_credits + ai_credits:,} CR")
            embed.add_field(name="Ventilation CR",value=f"Joueurs {player_credits:,} • IA {ai_credits:,}")
            embed.add_field(
                name="Flux admin 24 h",
                value=(f"Créés {int(s['admin_credit_sources']):,} CR • "
                       f"Détruits {int(s['admin_credit_sinks']):,} CR"),
                inline=False,
            )
            embed.add_field(name="Minerai produit",value=f"Joueurs {int(s['player_ore']):,} • IA {int(s['ai_ore']):,} ({float(s['ai_ore_percent']):.1f} %)")
            embed.add_field(name="Lingots produits",value=f"Joueurs {int(s['player_ingots']):,} • IA {int(s['ai_ingots']):,} ({float(s['ai_ingot_percent']):.1f} %)")
            embed.add_field(name="Marché 24 h",value=f"Volume {int(s['market_volume']):,} • moyen {float(s['market_average_price']):.2f} CR\nPart IA {float(s['ai_market_percent']):.1f} %")
            embed.add_field(name="Transports 24 h",value=f"Durée moyenne {float(s['average_delivery_minutes']):.1f} min\nPart IA {float(s['ai_transport_percent']):.1f} %")
            embed.add_field(name="Indicateur prix mondial",value=f"{int(s['world_price']):,} CR ({float(s['world_price_change_percent']):+.1f} % / 24 h)")
            embed.add_field(name="Transports actifs",value=f"{int(s['active_transports']):,}")
            embed.add_field(name="Contrats ouverts",value=f"{int(s['active_contracts']):,}")
            embed.add_field(name="Acteurs",value=f"Joueurs {int(s['player_actors']):,} • IA {int(s['ai_actors']):,}",inline=False)
            embed.add_field(name="Entreprises actives",value=f"Joueurs {int(s['active_player_companies']):,} • IA {int(s['active_ai_companies']):,}")
            embed.add_field(name="Métiers actifs",value=(f"⛏️ {int(s['active_miners']):,} • 🚚 {int(s['active_merchants']):,}\n"
                f"🔨 {int(s['active_blacksmiths']):,} • 🏦 {int(s['active_bankers']):,}"))
            await context.message.channel.send(embed=embed)
        except IndustrialEconomyError:
            logger.exception("[ECONOMY] Stats failed | User: %s",context.message.author.id)
            await context.message.channel.send("Impossible de charger les statistiques économiques.")
    return economy_command
