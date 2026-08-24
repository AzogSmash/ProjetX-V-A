import asyncio
import logging
from typing import Protocol

from economy_v2.models import IndustrialCompany, IndustrialUser
from economy_v2.repository import (
    IndustrialEconomyRepository,
    SupabaseIndustrialEconomyRepository,
)


logger = logging.getLogger(__name__)


class IndustrialEconomyError(RuntimeError):
    pass


class CompanyAlreadyExistsError(IndustrialEconomyError):
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
