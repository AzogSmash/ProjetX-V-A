import unicodedata

import discord

from economy_v2.jobs import JOB_TYPES, format_available_jobs, resolve_job
from economy_v2.router import EconomyCommandContext, EconomyCommandHandler
from economy_v2.services import CompanyAlreadyExistsError, IndustrialEconomyService


COMPANY_NAME_MIN_LENGTH = 3
COMPANY_NAME_MAX_LENGTH = 40


def validate_company_name(raw_name: str) -> tuple[str | None, str | None]:
    name = raw_name.strip()
    if len(name) < COMPANY_NAME_MIN_LENGTH:
        return None, "Le nom de l'entreprise doit contenir au moins 3 caractères."
    if len(name) > COMPANY_NAME_MAX_LENGTH:
        return None, "Le nom de l'entreprise ne peut pas dépasser 40 caractères."
    lowered = name.casefold()
    if "<@" in name or "<#" in name or "@everyone" in lowered or "@here" in lowered:
        return None, "Le nom de l'entreprise ne peut pas contenir de mention Discord."
    if any(unicodedata.category(character).startswith("C") for character in name):
        return None, "Le nom de l'entreprise contient un caractère non autorisé."
    return name, None


def _invalid_job_embed() -> discord.Embed:
    return discord.Embed(
        title="Métier invalide",
        description=f"**Métiers disponibles :**\n{format_available_jobs()}",
        color=0xE67E22,
    )


def build_company_command(service: IndustrialEconomyService) -> EconomyCommandHandler:
    async def company_command(context: EconomyCommandContext) -> None:
        if not context.args:
            await _show_company(context, service)
            return

        if context.args[0].casefold() != "create":
            await context.message.channel.send(
                "Utilise `?company` ou `?company create <métier> <nom>`."
            )
            return

        if len(context.args) < 3:
            await context.message.channel.send(
                "Syntaxe : `?company create <métier> <nom>`"
            )
            return

        job = resolve_job(context.args[1])
        if job is None:
            await context.message.channel.send(embed=_invalid_job_embed())
            return

        name, validation_error = validate_company_name(" ".join(context.args[2:]))
        if validation_error:
            await context.message.channel.send(validation_error)
            return

        try:
            company = await service.create_first_company(
                context.message.author.id,
                name,
                job.key,
            )
        except CompanyAlreadyExistsError:
            await context.message.channel.send(
                "Tu possèdes déjà une entreprise.\n"
                "Le système permettant d'acheter une nouvelle société arrivera plus tard."
            )
            return

        company_job = JOB_TYPES[company.job_type]
        embed = discord.Embed(title="🏢 Entreprise créée", color=0x2ECC71)
        embed.add_field(name="Nom", value=company.name, inline=False)
        embed.add_field(
            name="Métier",
            value=f"{company_job.emoji} {company_job.label}",
            inline=True,
        )
        embed.add_field(name="Niveau", value=str(company.level), inline=True)
        embed.set_footer(text="Ta première entreprise est gratuite.")
        await context.message.channel.send(embed=embed)

    return company_command


async def _show_company(
    context: EconomyCommandContext,
    service: IndustrialEconomyService,
) -> None:
    company = await service.get_primary_company(context.message.author.id)
    if company is None:
        await context.message.channel.send(
            "Tu ne possèdes encore aucune entreprise industrielle.\n\n"
            "Crée ta première entreprise gratuitement avec :\n"
            "`?company create <métier> <nom>`"
        )
        return

    job = JOB_TYPES[company.job_type]
    embed = discord.Embed(title=f"🏢 {company.name}", color=0xD68910)
    embed.add_field(name="Métier", value=f"{job.emoji} {job.label}", inline=True)
    embed.add_field(name="Niveau", value=str(company.level), inline=True)
    embed.add_field(
        name="Propriétaire",
        value=context.message.author.mention,
        inline=False,
    )
    await context.message.channel.send(embed=embed)
