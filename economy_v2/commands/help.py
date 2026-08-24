import discord

from economy_v2.router import EconomyCommandContext


async def ecohelp_command(context: EconomyCommandContext) -> None:
    embed = discord.Embed(
        title="Économie industrielle",
        description=(
            "Cette économie utilise ses propres crédits industriels (**CR**) "
            "et reste entièrement séparée de l'économie et du casino `!`."
        ),
        color=0xD68910,
    )
    embed.add_field(
        name="Commandes disponibles",
        value=(
            "`?wallet` — Consulter son compte économique industriel\n"
            "`?company` — Afficher son entreprise\n"
            "`?company create <métier> <nom>` — Créer gratuitement sa première entreprise"
        ),
        inline=False,
    )
    embed.add_field(
        name="En développement",
        value=(
            "⛏️ `?mine` — Mine\n"
            "📈 `?market` — Marché\n"
            "🔥 `?forge` — Forge\n"
            "🚚 `?delivery` — Livraisons\n"
            "🏦 `?bank` — Banque\n"
            "📜 `?contracts` — Contrats"
        ),
        inline=False,
    )
    embed.set_footer(text="Préfixe réservé à l'économie industrielle : ?")
    await context.message.channel.send(embed=embed)
