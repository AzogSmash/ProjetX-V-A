from typing import Protocol


class IndustrialWalletService(Protocol):
    """Contrat asynchrone de la future persistance des crédits industriels."""

    async def get_balance(self, user_id: int) -> int:
        ...


class PlaceholderIndustrialWalletService:
    """Implémentation temporaire, sans lien avec l'économie historique du bot."""

    async def get_balance(self, user_id: int) -> int:
        return 0
