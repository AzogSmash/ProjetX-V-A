from dataclasses import dataclass


@dataclass(frozen=True)
class JobDefinition:
    key: str
    label: str
    emoji: str
    aliases: frozenset[str]


JOB_TYPES: dict[str, JobDefinition] = {
    "miner": JobDefinition("miner", "Mineur", "⛏️", frozenset({"miner", "mineur"})),
    "merchant": JobDefinition("merchant", "Marchand", "🧳", frozenset({"merchant", "marchand"})),
    "blacksmith": JobDefinition("blacksmith", "Forgeron", "🔨", frozenset({"blacksmith", "forgeron"})),
    "banker": JobDefinition("banker", "Banquier", "🏦", frozenset({"banker", "banquier"})),
}

JOB_ALIASES = {
    alias.casefold(): definition.key
    for definition in JOB_TYPES.values()
    for alias in definition.aliases
}


def resolve_job(value: str) -> JobDefinition | None:
    key = JOB_ALIASES.get(value.strip().casefold())
    return JOB_TYPES.get(key) if key else None


def format_available_jobs() -> str:
    return "\n".join(
        f"{job.emoji} {next(alias for alias in job.aliases if alias != job.key)}"
        for job in JOB_TYPES.values()
    )
