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
        name="Compte et entreprise",
        value=(
            "`?wallet` — Consulter son compte économique industriel\n"
            "`?company` — Afficher son entreprise\n"
            "`?company create <métier> <nom>` — Créer gratuitement sa première entreprise"
        ),
        inline=False,
    )
    embed.add_field(
        name="⛏️ Mine et marché",
        value=(
            "`?mine` — Afficher sa mine *(Mineurs)*\n"
            "`?mine collect` — Récolter le minerai\n"
            "`?mine upgrade <type>` — Améliorer la mine\n"
            "`?market` — Consulter le marché industriel\n"
            "`?market <sell|buy> iron_ore <quantité> <prix>` — Échanger\n"
            "`?market orders` / `?market cancel <id>` — Gérer ses ordres"
        ),
        inline=False,
    )
    embed.add_field(
        name="🚚 Marchand et transports",
        value=(
            "`?merchant` — Gérer son entreprise Marchand\n"
            "`?merchant inventory` / `?merchant transports` — Logistique\n"
            "`?merchant transport <forgeron> iron_ore <quantité>` — Expédier\n"
            "`?merchant upgrade <type>` — Améliorer les camions\n"
            "`?merchant transport-ingots <id>` — Charger les lingots"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔨 Forge et expéditions",
        value=(
            "`?forge` / `?forge inventory` — Gérer sa forge\n"
            "`?forge process iron_ore <quantité>` — Transformer le minerai\n"
            "`?forge collect` / `?forge jobs` — Suivre la production\n"
            "`?forge upgrade <type>` — Améliorer la forge\n"
            "`?forge ai-supply iron_ore <quantité>` — Fournisseur IA de secours\n"
            "`?forge shipment create <marchand> <banquier> <quantité>` — Préparer des lingots\n"
            "`?forge shipment cancel <id>` — Annuler une expédition"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏦 Banque et systèmes communs",
        value=(
            "`?bank` / `?bank inventory` — Gérer sa banque\n"
            "`?bank market` — Consulter le marché mondial\n"
            "`?bank sell iron_ingot <quantité>` — Vendre des lingots\n"
            "`?bank ai-order iron_ingot <quantité>` — Forgeron IA de secours\n"
            "`?bank history` — Historique des ventes\n"
            "`?delivery list|stats` — Missions accessibles à tous\n"
            "`?delivery accept <id>` — Accélérer un transport\n"
            "`?contracts` / `?contracts mine` — Contrats de ressources\n"
            "`?contracts create <ressource> <quantité> <prix>` — Publier\n"
            "`?contracts accept|cancel <id>` — Gérer un contrat\n"
            "`?economy` — Activité joueurs, IA et indicateurs globaux"
        ),
        inline=False,
    )
    embed.add_field(
        name="Progression et suivi",
        value=(
            "`?next` — Recommandations dynamiques\n"
            "`?fiche` / `?cv` — Fiche industrielle\n"
            "`?rank` — Classements\n"
            "`?bilan` — Statistiques personnelles\n"
            "`?achievements` / `?objectives` — Progression\n"
            "`?orders` — Opérations en cours\n"
            "`?partners` / `?notifications` — Réseau et préférences"
        ),
        inline=False,
    )
    embed.set_footer(text="Préfixe réservé à l'économie industrielle : ?")
    await context.message.channel.send(embed=embed)
