import asyncio
import logging
from typing import Protocol

from economy_v2.models import (
    IndustrialCompany,
    IndustrialUser,
    InventoryEntry,
    Mine,
    MineCollectionResult,
    MineUpgradeResult,
    MarketOrder,
    MarketOrderResult,
    MarketSummary,
)
from economy_v2.repository import (
    IndustrialEconomyRepository,
    SupabaseIndustrialEconomyRepository,
)


logger = logging.getLogger(__name__)


class IndustrialEconomyError(RuntimeError):
    pass


class CompanyAlreadyExistsError(IndustrialEconomyError):
    pass


class MineAccessDeniedError(IndustrialEconomyError):
    def __init__(self, current_job: str | None) -> None:
        super().__init__(current_job)
        self.current_job = current_job


class MinerCompanyRequiredError(IndustrialEconomyError):
    pass


class InsufficientIndustrialFundsError(IndustrialEconomyError):
    def __init__(self, cost: int, balance: int) -> None:
        super().__init__(cost, balance)
        self.cost = cost
        self.balance = balance


class MineUpgradeMaxLevelError(IndustrialEconomyError):
    pass


class MarketAccessDeniedError(IndustrialEconomyError):
    def __init__(self, required_job: str, current_job: str | None) -> None:
        self.required_job, self.current_job = required_job, current_job


class MarketInsufficientAssetsError(IndustrialEconomyError):
    def __init__(self, available: int) -> None:
        self.available = available


class MarketOrderLimitError(IndustrialEconomyError):
    pass


class MarketOrderNotFoundError(IndustrialEconomyError):
    pass


class MarketOrderClosedError(IndustrialEconomyError):
    pass


class IndustrialWalletService(Protocol):
    async def get_balance(self, user_id: int) -> int:
        ...


class IndustrialEconomyService(IndustrialWalletService, Protocol):
    async def get_or_create_user(self, user_id: int) -> IndustrialUser:
        ...

    async def get_primary_company(self, user_id: int) -> IndustrialCompany | None:
        ...

    async def create_first_company(
        self,
        user_id: int,
        name: str,
        job_type: str,
    ) -> IndustrialCompany:
        ...

    async def get_or_create_mine(self, user_id: int) -> Mine:
        ...

    async def refresh_mine(self, user_id: int) -> Mine:
        ...

    async def collect_mine(self, user_id: int) -> MineCollectionResult:
        ...

    async def upgrade_mine(self, user_id: int, upgrade_type: str) -> MineUpgradeResult:
        ...

    async def get_inventory(self, user_id: int) -> list[InventoryEntry]:
        ...

    async def create_market_order(
        self, user_id: int, side: str, resource_type: str,
        quantity: int, unit_price: int, request_id: str,
    ) -> MarketOrderResult:
        ...

    async def cancel_market_order(self, user_id: int, order_id: int) -> MarketOrder:
        ...

    async def get_market_orders(self, user_id: int) -> list[MarketOrder]:
        ...

    async def get_market_summary(
        self, resource_type: str, depth: int
    ) -> MarketSummary:
        ...


class SupabaseIndustrialEconomyService:
    def __init__(self, repository: IndustrialEconomyRepository | None = None) -> None:
        self._repository = repository or SupabaseIndustrialEconomyRepository()

    async def _run(self, operation: str, user_id: int, function, *args):
        try:
            return await asyncio.to_thread(function, *args)
        except IndustrialEconomyError:
            raise
        except Exception as error:
            logger.exception(
                "[ECONOMY] Database operation failed: %s | User: %s",
                operation,
                user_id,
            )
            raise IndustrialEconomyError(operation) from error

    async def get_or_create_user(self, user_id: int) -> IndustrialUser:
        return await self._run(
            "get_or_create_user",
            user_id,
            self._repository.get_or_create_user,
            user_id,
        )

    async def get_balance(self, user_id: int) -> int:
        user = await self.get_or_create_user(user_id)
        return user.credits

    async def get_primary_company(self, user_id: int) -> IndustrialCompany | None:
        await self.get_or_create_user(user_id)
        return await self._run(
            "get_primary_company",
            user_id,
            self._repository.get_primary_company,
            user_id,
        )

    async def create_first_company(
        self,
        user_id: int,
        name: str,
        job_type: str,
    ) -> IndustrialCompany:
        status, company = await self._run(
            "create_first_company",
            user_id,
            self._repository.create_first_company,
            user_id,
            name,
            job_type,
        )
        if status == "already_exists":
            raise CompanyAlreadyExistsError
        if status != "created" or company is None:
            raise IndustrialEconomyError("unexpected create_first_company result")
        return company

    @staticmethod
    def _raise_mine_status(status: str, current_job: str | None) -> None:
        if status == "not_miner":
            raise MineAccessDeniedError(current_job)
        if status == "no_miner_company":
            raise MinerCompanyRequiredError
        raise IndustrialEconomyError(f"unexpected mine status: {status}")

    async def get_or_create_mine(self, user_id: int) -> Mine:
        status, current_job, mine = await self._run(
            "get_or_create_and_refresh_mine",
            user_id,
            self._repository.get_or_create_and_refresh_mine,
            user_id,
        )
        if status != "ok" or mine is None:
            self._raise_mine_status(status, current_job)
        return mine

    async def refresh_mine(self, user_id: int) -> Mine:
        return await self.get_or_create_mine(user_id)

    async def collect_mine(self, user_id: int) -> MineCollectionResult:
        status, current_job, result = await self._run(
            "collect_mine",
            user_id,
            self._repository.collect_mine,
            user_id,
        )
        if status != "ok" or result is None:
            self._raise_mine_status(status, current_job)
        return result

    async def upgrade_mine(self, user_id: int, upgrade_type: str) -> MineUpgradeResult:
        status, current_job, cost, balance, result = await self._run(
            "upgrade_mine",
            user_id,
            self._repository.upgrade_mine,
            user_id,
            upgrade_type,
        )
        if status == "insufficient_funds":
            raise InsufficientIndustrialFundsError(cost or 0, balance or 0)
        if status == "max_level":
            raise MineUpgradeMaxLevelError
        if status != "ok" or result is None:
            self._raise_mine_status(status, current_job)
        return result

    async def get_inventory(self, user_id: int) -> list[InventoryEntry]:
        return await self._run(
            "get_inventory",
            user_id,
            self._repository.get_inventory,
            user_id,
        )

    async def create_market_order(self, user_id: int, side: str, resource_type: str,
                                  quantity: int, unit_price: int, request_id: str) -> MarketOrderResult:
        status, result, available = await self._run("create_market_order", user_id,
            self._repository.create_market_order, user_id, side, resource_type, quantity, unit_price, request_id)
        if status in {"not_miner", "not_merchant"}:
            raise MarketAccessDeniedError("miner" if side == "sell" else "merchant", None)
        if status in {"insufficient_inventory", "insufficient_funds"}:
            raise MarketInsufficientAssetsError(available or 0)
        if status == "order_limit": raise MarketOrderLimitError
        if result is None: raise IndustrialEconomyError(f"unexpected market status: {status}")
        return result

    async def cancel_market_order(self, user_id: int, order_id: int) -> MarketOrder:
        status, order = await self._run("cancel_market_order", user_id,
            self._repository.cancel_market_order, user_id, order_id)
        if status == "not_found": raise MarketOrderNotFoundError
        if status == "already_closed": raise MarketOrderClosedError
        if status != "ok" or order is None: raise IndustrialEconomyError(f"unexpected cancel status: {status}")
        return order

    async def get_market_orders(self, user_id: int) -> list[MarketOrder]:
        return await self._run("get_market_orders", user_id, self._repository.get_market_orders, user_id)

    async def get_market_summary(self, resource_type: str, depth: int) -> MarketSummary:
        return await self._run("get_market_summary", 0, self._repository.get_market_summary, resource_type, depth)
