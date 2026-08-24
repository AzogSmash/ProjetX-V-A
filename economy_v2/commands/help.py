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
            "\n`?mine` — Afficher sa mine *(Mineurs)*\n"
            "`?mine collect` — Récolter le minerai\n"
            "`?mine upgrade <type>` — Améliorer la mine\n"
            "`?market` — Consulter le marché industriel\n"
            "`?market <sell|buy> iron_ore <quantité> <prix>` — Échanger\n"
            "`?market orders` / `?market cancel <id>` — Gérer ses ordres"
            "\n`?merchant` — Gérer son entreprise Marchand\n"
            "`?merchant inventory` / `?merchant transports` — Logistique\n"
            "`?merchant transport <forgeron> iron_ore <quantité>` — Expédier\n"
            "`?merchant upgrade <type>` — Améliorer les camions"
            "\n`?forge` / `?forge inventory` — Gérer sa forge\n"
            "`?forge process iron_ore <quantité>` — Transformer le minerai\n"
            "`?forge collect` / `?forge jobs` — Suivre la production\n"
            "`?forge upgrade <type>` — Améliorer la forge"
        ),
        inline=False,
    )
    embed.add_field(
        name="En développement",
        value=(
            "🚚 `?delivery` — Livraisons\n"
            "🏦 `?bank` — Banque\n"
            "📜 `?contracts` — Contrats"
        ),
        inline=False,
    )
    embed.set_footer(text="Préfixe réservé à l'économie industrielle : ?")
    await context.message.channel.send(embed=embed)
