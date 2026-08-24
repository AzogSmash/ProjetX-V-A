import logging

import discord

from economy_v2.contracts_config import valid_contract_values
from economy_v2.resources import get_resource
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import IndustrialEconomyError, IndustrialEconomyService, ShipmentError


logger = logging.getLogger(__name__)


def build_contracts_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def contracts_command(context: EconomyCommandContext) -> None:
        try:
            action = context.args[0].casefold() if context.args else "list"
            if action in {"list", "mine"} and len(context.args) <= 1:
                contracts = await service.get_contracts(context.message.author.id, action == "mine")
                lines = [f"`#{c.id}` {c.quantity:,} {get_resource(c.resource_type).label} — "
                         f"**{c.total_price:,} CR** — {c.status}" for c in contracts]
                await context.message.channel.send(embed=discord.Embed(
                    title="📜 Mes contrats" if action == "mine" else "📜 Contrats ouverts",
                    description="\n".join(lines) or "Aucun contrat.", color=0x8E44AD))
            elif action == "create" and len(context.args) == 4:
                await _create(context, service)
            elif action in {"accept", "cancel"} and len(context.args) == 2:
                try: contract_id = int(context.args[1])
                except ValueError: contract_id = 0
                if contract_id < 1:
                    await context.message.channel.send("ID de contrat invalide."); return
                try:
                    if action == "accept":
                        contract = await service.accept_contract(context.message.author.id, contract_id,
                                                                 f"discord:{context.message.id}")
                        text = f"✅ Contrat #{contract.id} exécuté : **{contract.total_price:,} CR** transférés."
                    else:
                        contract = await service.cancel_contract(context.message.author.id, contract_id,
                                                                 f"discord:{context.message.id}")
                        text = f"✅ Contrat #{contract.id} annulé et escrow remboursé."
                    await context.message.channel.send(text)
                except ShipmentError as error:
                    messages = {"not_found": "Contrat introuvable.", "already_closed": "Contrat déjà fermé.",
                                "not_owner": "Ce contrat ne t'appartient pas.",
                                "own_contract": "Tu ne peux pas accepter ton propre contrat.",
                                "insufficient_inventory": f"Stock insuffisant : **{error.available or 0:,}**."}
                    await context.message.channel.send(messages.get(error.reason, "Action impossible."))
            else:
                await context.message.channel.send(
                    "Syntaxe : `?contracts`, `?contracts create <ressource> <quantité> <prix_total>`, "
                    "`?contracts accept <id>`, `?contracts mine`, `?contracts cancel <id>`."
                )
        except IndustrialEconomyError:
            logger.exception("[ECONOMY] Contract operation failed | User: %s", context.message.author.id)
            await context.message.channel.send("Une erreur est survenue avec les contrats.")
    return contracts_command


async def _create(context, service) -> None:
    resource = get_resource(context.args[1])
    try: quantity, total = int(context.args[2]), int(context.args[3])
    except ValueError: quantity = total = 0
    if resource is None or not valid_contract_values(quantity, total):
        await context.message.channel.send("Ressource, quantité ou prix total invalide."); return
    try:
        contract = await service.create_contract(context.message.author.id, resource.resource_type,
                                                 quantity, total, f"discord:{context.message.id}")
    except ShipmentError as error:
        if error.reason == "insufficient_funds":
            await context.message.channel.send(f"Fonds insuffisants. Solde : **{error.available or 0:,} CR**.")
        elif error.reason == "contract_limit":
            await context.message.channel.send("Tu as atteint la limite de contrats ouverts.")
        else: await context.message.channel.send("Impossible de créer ce contrat.")
        return
    await context.message.channel.send(
        f"📜 **Contrat #{contract.id} créé**\n{contract.quantity:,} {resource.label}\n"
        f"Escrow : **{contract.total_price:,} CR**")
