from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

import discord


ECONOMY_PREFIX = "?"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedEconomyCommand:
    command: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class EconomyCommandContext:
    message: discord.Message
    args: tuple[str, ...]
    router: "EconomyRouter"


EconomyCommandHandler = Callable[[EconomyCommandContext], Awaitable[None]]


def parse_economy_message(content: str) -> ParsedEconomyCommand | None:
    stripped = content.lstrip()
    if not stripped.startswith(ECONOMY_PREFIX):
        return None

    parts = stripped[len(ECONOMY_PREFIX):].strip().split()
    if not parts:
        return ParsedEconomyCommand(command="", args=())
    return ParsedEconomyCommand(command=parts[0].casefold(), args=tuple(parts[1:]))


class EconomyRouter:
    def __init__(self) -> None:
        self._commands: dict[str, EconomyCommandHandler] = {}

    @property
    def command_names(self) -> frozenset[str]:
        return frozenset(self._commands)

    def register_command(self, name: str, handler: EconomyCommandHandler) -> None:
        normalized = name.strip().casefold()
        if not normalized or any(char.isspace() for char in normalized):
            raise ValueError(f"Nom de commande économique invalide : {name!r}")
        if normalized in self._commands:
            raise RuntimeError(f"Commande économique déjà enregistrée : {normalized!r}")
        self._commands[normalized] = handler

    async def handle(self, message: discord.Message) -> bool:
        parsed = parse_economy_message(message.content or "")
        if parsed is None:
            return False

        command_name = parsed.command or "ecohelp"
        handler = self._commands.get(command_name)
        if handler is None:
            await message.channel.send(
                "Commande économique inconnue.\n"
                "Utilise `?ecohelp` pour voir les commandes disponibles."
            )
            return True

        logger.info("[ECONOMY] Command: %s | User: %s", command_name, message.author.id)
        context = EconomyCommandContext(message=message, args=parsed.args, router=self)
        try:
            await handler(context)
        except Exception:
            logger.exception(
                "[ECONOMY] Command failed: %s | User: %s",
                command_name,
                message.author.id,
            )
            await message.channel.send(
                "Une erreur est survenue lors de l'accès à l'économie industrielle.\n"
                "Réessaie dans quelques instants."
            )
        return True


def _legacy_command_names(bot) -> set[str]:
    names: set[str] = set()
    for command in bot.walk_commands():
        names.add(command.name.casefold())
        names.update(alias.casefold() for alias in command.aliases)
    return names


def find_command_name_collisions(bot, economy_names: Iterable[str]) -> set[str]:
    normalized_economy_names = {name.strip().casefold() for name in economy_names}
    return _legacy_command_names(bot) & normalized_economy_names


def validate_command_names(bot, economy_names: Iterable[str]) -> None:
    collisions = find_command_name_collisions(bot, economy_names)
    if collisions:
        names = ", ".join(f'"{name}"' for name in sorted(collisions))
        raise RuntimeError(f"Conflit détecté entre les commandes ! et ? : {names}")
