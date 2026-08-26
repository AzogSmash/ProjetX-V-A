import asyncio
import logging
from typing import Protocol

from economy_v2.models import (
    IndustrialActor,
    IndustrialCompany,
    IndustrialUser,
    InventoryEntry,
    Mine,
    MineCollectionResult,
    MineUpgradeResult,
    MarketOrder,
    MarketOrderResult,
    MarketSummary,
    Merchant,
    MerchantTransportResult,
    MerchantUpgradeResult,
    IndustrialTransport,
    Blacksmith,
    ForgeCollectionResult,
    ForgeJob,
    ForgeProcessResult,
    ForgeUpgradeResult,
    ShipmentResult,
    Banker,
    WorldSale,
    DeliveryMission,
    DeliveryProfile,
    IndustrialContract,
)
from economy_v2.repository import (
    IndustrialEconomyRepository,
    SQLiteIndustrialEconomyRepository,
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


class MerchantAccessDeniedError(IndustrialEconomyError):
    def __init__(self, current_job: str | None) -> None:
        self.current_job = current_job


class MerchantCompanyRequiredError(IndustrialEconomyError):
    pass


class MerchantUpgradeMaxLevelError(IndustrialEconomyError):
    pass


class MerchantTransportError(IndustrialEconomyError):
    def __init__(self, reason: str, available: int | None = None) -> None:
        self.reason, self.available = reason, available


class BlacksmithAccessDeniedError(IndustrialEconomyError):
    def __init__(self, current_job: str | None) -> None:
        self.current_job = current_job


class BlacksmithCompanyRequiredError(IndustrialEconomyError):
    pass


class ForgeUpgradeMaxLevelError(IndustrialEconomyError):
    pass


class ForgeProcessError(IndustrialEconomyError):
    def __init__(self, reason: str, available: int | None = None) -> None:
        self.reason, self.available = reason, available


class ShipmentError(IndustrialEconomyError):
    def __init__(self, reason: str, available: int | None = None) -> None:
        self.reason, self.available = reason, available


class BankerAccessDeniedError(IndustrialEconomyError):
    def __init__(self, current_job: str | None) -> None:
        self.current_job = current_job


class WorldSaleError(IndustrialEconomyError):
    def __init__(self, reason: str, available: int | None = None) -> None:
        self.reason, self.available = reason, available


class IndustrialWalletService(Protocol):
    async def get_balance(self, user_id: int) -> int:
        ...


class IndustrialEconomyService(IndustrialWalletService, Protocol):
    async def get_player_actor(self, user_id: int) -> IndustrialActor: ...

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

    async def upgrade_mine(self, user_id: int, upgrade_type: str,
                           request_id: str | None = None) -> MineUpgradeResult:
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

    async def get_or_create_merchant(self, user_id: int) -> Merchant: ...
    async def upgrade_merchant(self, user_id: int, upgrade_type: str, request_id: str) -> MerchantUpgradeResult: ...
    async def start_transport(self, user_id: int, receiver_user_id: int, resource_type: str,
                              quantity: int, request_id: str) -> MerchantTransportResult: ...
    async def get_merchant_transports(self, user_id: int) -> list[IndustrialTransport]: ...
    async def get_or_create_blacksmith(self, user_id: int) -> Blacksmith: ...
    async def start_forge_job(self, user_id: int, resource_type: str, quantity: int, request_id: str) -> ForgeProcessResult: ...
    async def collect_forge_jobs(self, user_id: int, request_id: str) -> ForgeCollectionResult: ...
    async def upgrade_forge(self, user_id: int, upgrade_type: str, request_id: str) -> ForgeUpgradeResult: ...
    async def get_forge_jobs(self, user_id: int) -> list[ForgeJob]: ...
    async def create_ingot_shipment(self, user_id: int, merchant_id: int, banker_id: int,
                                    quantity: int, request_id: str) -> ShipmentResult: ...
    async def cancel_ingot_shipment(self, user_id: int, shipment_id: int,
                                    request_id: str) -> ShipmentResult: ...
    async def accept_ingot_shipment(self, user_id: int, shipment_id: int,
                                    request_id: str) -> ShipmentResult: ...
    async def get_or_create_banker(self, user_id: int) -> Banker: ...
    async def sell_world_ingots(self, user_id: int, quantity: int, request_id: str) -> WorldSale: ...
    async def get_world_market(self) -> dict: ...
    async def get_world_sales(self, user_id: int) -> list[WorldSale]: ...
    async def get_delivery_missions(self) -> list[DeliveryMission]: ...
    async def get_delivery_profile(self, user_id: int) -> DeliveryProfile: ...
    async def accept_delivery(self, user_id: int, mission_id: int, request_id: str) -> dict: ...
    async def create_contract(self, user_id: int, resource: str, quantity: int,
                              total: int, request_id: str, target_user_id: int | None = None) -> IndustrialContract: ...
    async def accept_contract(self, user_id: int, contract_id: int, request_id: str) -> IndustrialContract: ...
    async def cancel_contract(self, user_id: int, contract_id: int, request_id: str) -> IndustrialContract: ...
    async def get_contracts(self, user_id: int, mine: bool = False) -> list[IndustrialContract]: ...
    async def record_activity(self, user_id: int) -> None: ...
    async def evaluate_ai_companies(self) -> list[dict]: ...
    async def purchase_ai_supply(self, user_id: int, resource_type: str,
                                 quantity: int, request_id: str) -> dict: ...
    async def get_economy_stats(self) -> dict: ...
    async def adjust_admin_credits(self, admin_user_id: int, target_user_id: int,
                                   operation: str, amount: int, request_id: str): ...
    async def get_next_actions_snapshot(self, user_id: int) -> dict: ...
    async def get_industrial_profile(self, user_id: int): ...
    async def get_rankings(self, category: str, limit: int = 10): ...
    async def refresh_achievements(self, user_id: int): ...
    async def get_objectives(self, user_id: int, now: int | None = None): ...
    async def get_player_stats(self, user_id: int): ...
    async def get_orders_overview(self, user_id: int): ...
    async def update_partnership(self, user_id: int, target_id: int, action: str, request_id: str): ...
    async def get_partnerships(self, user_id: int): ...
    async def get_notification_preferences(self, user_id: int): ...
    async def set_notification_preference(self, user_id: int, category: str, enabled: bool): ...
    async def get_admin_log(self, user_id: int, limit: int = 20): ...
    async def economy_check(self): ...
    async def get_season_dashboard(self, user_id: int, category: str = "overall"): ...
    async def get_season_history(self): ...
    async def refresh_titles(self, user_id: int): ...
    async def equip_title(self, user_id: int, selector: str, request_id: str): ...
    async def remove_title(self, user_id: int, request_id: str): ...
    async def get_active_events(self): ...
    async def get_team(self, user_id: int): ...
    async def invite_team_member(self, user_id: int, target_id: int, request_id: str): ...
    async def resolve_team_invitation(self, user_id: int, invitation_id: int, action: str, request_id: str): ...
    async def change_team(self, user_id: int, action: str, target_id: int | None, role: str | None, request_id: str): ...
    async def get_economy_report(self): ...
    async def get_tutorial(self,user_id:int): ...
    async def update_tutorial(self,user_id:int,action:str,request_id:str): ...


class SQLiteIndustrialEconomyService:
    def __init__(self, repository: IndustrialEconomyRepository | None = None) -> None:
        self._repository = repository or SQLiteIndustrialEconomyRepository()
        if repository is None:
            self._repository.ensure_current_event()

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

    async def get_player_actor(self, user_id: int) -> IndustrialActor:
        return await self._run("get_player_actor", user_id,
                               self._repository.get_or_create_player_actor, user_id)

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

    async def upgrade_mine(self, user_id: int, upgrade_type: str,
                           request_id: str | None = None) -> MineUpgradeResult:
        status, current_job, cost, balance, result = await self._run(
            "upgrade_mine",
            user_id,
            self._repository.upgrade_mine,
            user_id,
            upgrade_type,
            *([request_id] if request_id else []),
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

    @staticmethod
    def _raise_merchant_status(status: str, current_job: str | None) -> None:
        if status == "not_merchant": raise MerchantAccessDeniedError(current_job)
        if status == "no_merchant_company": raise MerchantCompanyRequiredError
        raise IndustrialEconomyError(f"unexpected merchant status: {status}")

    async def get_or_create_merchant(self, user_id: int) -> Merchant:
        status, current_job, merchant = await self._run(
            "get_or_create_merchant", user_id, self._repository.get_or_create_merchant, user_id)
        if status != "ok" or merchant is None:
            self._raise_merchant_status(status, current_job)
        return merchant

    async def upgrade_merchant(self, user_id: int, upgrade_type: str,
                               request_id: str) -> MerchantUpgradeResult:
        status, current_job, cost, balance, result = await self._run(
            "upgrade_merchant", user_id, self._repository.upgrade_merchant,
            user_id, upgrade_type, request_id)
        if status == "insufficient_funds":
            raise InsufficientIndustrialFundsError(cost or 0, balance or 0)
        if status == "max_level": raise MerchantUpgradeMaxLevelError
        if status not in {"ok", "duplicate"} or result is None:
            self._raise_merchant_status(status, current_job)
        return result

    async def start_transport(self, user_id: int, receiver_user_id: int,
                              resource_type: str, quantity: int,
                              request_id: str) -> MerchantTransportResult:
        status, current_job, available, result = await self._run(
            "start_transport", user_id, self._repository.start_transport,
            user_id, receiver_user_id, resource_type, quantity, request_id)
        if status in {"not_merchant", "no_merchant_company"}:
            self._raise_merchant_status(status, current_job)
        if status not in {"ok", "duplicate"} or result is None:
            raise MerchantTransportError(status, available)
        return result

    async def get_merchant_transports(self, user_id: int) -> list[IndustrialTransport]:
        status, current_job, transports = await self._run(
            "get_merchant_transports", user_id,
            self._repository.get_merchant_transports, user_id)
        if status != "ok": self._raise_merchant_status(status, current_job)
        return transports

    @staticmethod
    def _raise_blacksmith_status(status: str, current_job: str | None) -> None:
        if status == "not_blacksmith": raise BlacksmithAccessDeniedError(current_job)
        if status == "no_blacksmith_company": raise BlacksmithCompanyRequiredError
        raise IndustrialEconomyError(f"unexpected blacksmith status: {status}")

    async def get_or_create_blacksmith(self, user_id: int) -> Blacksmith:
        status, current_job, blacksmith = await self._run(
            "get_or_create_blacksmith", user_id,
            self._repository.get_or_create_blacksmith, user_id)
        if status != "ok" or blacksmith is None:
            self._raise_blacksmith_status(status, current_job)
        return blacksmith

    async def start_forge_job(self, user_id: int, resource_type: str,
                              quantity: int, request_id: str) -> ForgeProcessResult:
        status, current_job, available, result = await self._run(
            "start_forge_job", user_id, self._repository.start_forge_job,
            user_id, resource_type, quantity, request_id)
        if status in {"not_blacksmith", "no_blacksmith_company"}:
            self._raise_blacksmith_status(status, current_job)
        if status not in {"ok", "duplicate"} or result is None:
            raise ForgeProcessError(status, available)
        return result

    async def collect_forge_jobs(self, user_id: int,
                                 request_id: str) -> ForgeCollectionResult:
        status, current_job, result = await self._run(
            "collect_forge_jobs", user_id, self._repository.collect_forge_jobs,
            user_id, request_id)
        if status in {"not_blacksmith", "no_blacksmith_company"}:
            self._raise_blacksmith_status(status, current_job)
        if status not in {"ok", "duplicate"} or result is None:
            raise IndustrialEconomyError(f"unexpected forge collection status: {status}")
        return result

    async def upgrade_forge(self, user_id: int, upgrade_type: str,
                            request_id: str) -> ForgeUpgradeResult:
        status, current_job, cost, balance, result = await self._run(
            "upgrade_forge", user_id, self._repository.upgrade_forge,
            user_id, upgrade_type, request_id)
        if status == "insufficient_funds":
            raise InsufficientIndustrialFundsError(cost or 0, balance or 0)
        if status == "max_level": raise ForgeUpgradeMaxLevelError
        if status not in {"ok", "duplicate"} or result is None:
            self._raise_blacksmith_status(status, current_job)
        return result

    async def get_forge_jobs(self, user_id: int) -> list[ForgeJob]:
        status, current_job, jobs = await self._run(
            "get_forge_jobs", user_id, self._repository.get_forge_jobs, user_id)
        if status != "ok": self._raise_blacksmith_status(status, current_job)
        return jobs

    async def create_ingot_shipment(self, user_id: int, merchant_id: int, banker_id: int,
                                    quantity: int, request_id: str) -> ShipmentResult:
        status, current_job, available, result = await self._run(
            "create_ingot_shipment", user_id, self._repository.create_ingot_shipment,
            user_id, merchant_id, banker_id, quantity, request_id)
        if status in {"not_blacksmith", "no_blacksmith_company"}:
            self._raise_blacksmith_status(status, current_job)
        if status not in {"ok", "duplicate"} or result is None:
            raise ShipmentError(status, available)
        return result

    async def cancel_ingot_shipment(self, user_id: int, shipment_id: int,
                                    request_id: str) -> ShipmentResult:
        status, current_job, result = await self._run(
            "cancel_ingot_shipment", user_id, self._repository.cancel_ingot_shipment,
            user_id, shipment_id, request_id)
        if status in {"not_blacksmith", "no_blacksmith_company"}:
            self._raise_blacksmith_status(status, current_job)
        if status not in {"ok", "duplicate"} or result is None:
            raise ShipmentError(status)
        return result

    async def accept_ingot_shipment(self, user_id: int, shipment_id: int,
                                    request_id: str) -> ShipmentResult:
        status, current_job, available, result = await self._run(
            "accept_ingot_shipment", user_id, self._repository.accept_ingot_shipment,
            user_id, shipment_id, request_id)
        if status in {"not_merchant", "no_merchant_company"}:
            self._raise_merchant_status(status, current_job)
        if status not in {"ok", "duplicate"} or result is None:
            raise ShipmentError(status, available)
        return result

    @staticmethod
    def _raise_banker_status(status: str, current_job: str | None) -> None:
        if status == "not_banker": raise BankerAccessDeniedError(current_job)
        raise IndustrialEconomyError(f"unexpected banker status: {status}")

    async def get_or_create_banker(self, user_id: int) -> Banker:
        status, current_job, banker = await self._run(
            "get_or_create_banker", user_id, self._repository.get_or_create_banker, user_id)
        if status != "ok" or banker is None: self._raise_banker_status(status, current_job)
        return banker

    async def sell_world_ingots(self, user_id: int, quantity: int,
                                request_id: str) -> WorldSale:
        status, current_job, available, sale = await self._run(
            "sell_world_ingots", user_id, self._repository.sell_world_ingots,
            user_id, quantity, request_id)
        if status == "not_banker": self._raise_banker_status(status, current_job)
        if status not in {"ok", "duplicate"} or sale is None:
            raise WorldSaleError(status, available)
        return sale

    async def get_world_market(self) -> dict:
        return await self._run("get_world_market", 0, self._repository.get_world_market)

    async def get_world_sales(self, user_id: int) -> list[WorldSale]:
        await self.get_or_create_banker(user_id)
        return await self._run("get_world_sales", user_id, self._repository.get_world_sales, user_id)

    async def get_delivery_missions(self) -> list[DeliveryMission]:
        return await self._run("get_delivery_missions", 0, self._repository.get_delivery_missions)

    async def get_delivery_profile(self, user_id: int) -> DeliveryProfile:
        await self.get_or_create_user(user_id)
        return await self._run("get_delivery_profile", user_id,
                               self._repository.get_delivery_profile, user_id)

    async def accept_delivery(self, user_id: int, mission_id: int, request_id: str) -> dict:
        row = await self._run("accept_delivery", user_id, self._repository.accept_delivery,
                              user_id, mission_id, request_id)
        if row["result_status"] not in {"ok", "duplicate"}:
            raise ShipmentError(row["result_status"])
        return row

    async def create_contract(self, user_id: int, resource: str, quantity: int,
                              total: int, request_id: str, target_user_id: int | None = None) -> IndustrialContract:
        status, available, contract = await self._run("create_contract", user_id,
            self._repository.create_contract, user_id, resource, quantity, total, request_id, target_user_id)
        if status not in {"ok", "duplicate"} or contract is None:
            raise ShipmentError(status, int(available or 0))
        return contract

    async def accept_contract(self, user_id: int, contract_id: int,
                              request_id: str) -> IndustrialContract:
        status, available, contract = await self._run("accept_contract", user_id,
            self._repository.accept_contract, user_id, contract_id, request_id)
        if status not in {"ok", "duplicate"} or contract is None:
            raise ShipmentError(status, int(available or 0))
        return contract

    async def cancel_contract(self, user_id: int, contract_id: int,
                              request_id: str) -> IndustrialContract:
        status, contract = await self._run("cancel_contract", user_id,
            self._repository.cancel_contract, user_id, contract_id, request_id)
        if status not in {"ok", "duplicate"} or contract is None: raise ShipmentError(status)
        return contract

    async def get_contracts(self, user_id: int, mine: bool = False) -> list[IndustrialContract]:
        await self.get_or_create_user(user_id)
        return await self._run("get_contracts", user_id, self._repository.get_contracts, user_id, mine)

    async def record_activity(self, user_id: int) -> None:
        if not hasattr(self._repository, "record_activity"):
            return
        await self._run("record_activity", user_id, self._repository.record_activity, user_id)

    async def evaluate_ai_companies(self) -> list[dict]:
        return await self._run("evaluate_ai_companies", 0, self._repository.evaluate_ai_companies)

    async def purchase_ai_supply(self, user_id: int, resource_type: str,
                                 quantity: int, request_id: str) -> dict:
        row = await self._run("purchase_ai_supply", user_id,
            self._repository.purchase_ai_supply, user_id, resource_type, quantity, request_id)
        if row["result_status"] not in {"ok", "duplicate"}:
            raise ShipmentError(row["result_status"], int(row.get("available_amount") or 0))
        return row

    async def get_economy_stats(self) -> dict:
        stats = await self._run("get_economy_stats",0,self._repository.get_economy_stats)
        admin_stats = await self._run(
            "get_admin_credit_stats", 0, self._repository.get_admin_credit_stats,
        )
        return stats | admin_stats

    async def adjust_admin_credits(self, admin_user_id: int, target_user_id: int,
                                   operation: str, amount: int, request_id: str):
        return await self._run(
            "adjust_admin_credits", admin_user_id,
            self._repository.adjust_admin_credits,
            admin_user_id, target_user_id, operation, amount, request_id,
        )

    async def get_next_actions_snapshot(self, user_id: int) -> dict:
        return await self._run(
            "get_next_actions_snapshot", user_id,
            self._repository.get_next_actions_snapshot, user_id,
        )

    async def get_industrial_profile(self, user_id): return await self._run("get_industrial_profile", user_id, self._repository.get_industrial_profile, user_id)
    async def get_rankings(self, category, limit=10): return await self._run("get_rankings", 0, self._repository.get_rankings, category, limit)
    async def refresh_achievements(self, user_id): return await self._run("refresh_achievements", user_id, self._repository.refresh_achievements, user_id)
    async def get_objectives(self, user_id, now=None):
        args = (user_id,) if now is None else (user_id, now)
        return await self._run("get_objectives", user_id, self._repository.get_objectives, *args)
    async def get_player_stats(self, user_id): return await self._run("get_player_stats", user_id, self._repository.get_player_stats, user_id)
    async def get_orders_overview(self, user_id): return await self._run("get_orders_overview", user_id, self._repository.get_orders_overview, user_id)
    async def update_partnership(self, user_id, target_id, action, request_id): return await self._run("update_partnership", user_id, self._repository.update_partnership, user_id, target_id, action, request_id)
    async def get_partnerships(self, user_id): return await self._run("get_partnerships", user_id, self._repository.get_partnerships, user_id)
    async def get_notification_preferences(self, user_id): return await self._run("get_notification_preferences", user_id, self._repository.get_notification_preferences, user_id)
    async def set_notification_preference(self, user_id, category, enabled): return await self._run("set_notification_preference", user_id, self._repository.set_notification_preference, user_id, category, enabled)
    async def get_admin_log(self, user_id, limit=20): return await self._run("get_admin_log", user_id, self._repository.get_admin_log, user_id, limit)
    async def economy_check(self): return await self._run("economy_check", 0, self._repository.economy_check)
    async def get_season_dashboard(self,user_id,category="overall"):return await self._run("get_season_dashboard",user_id,self._repository.get_season_dashboard,user_id,category)
    async def get_season_history(self):return await self._run("get_season_history",0,self._repository.get_season_history)
    async def refresh_titles(self,user_id):return await self._run("refresh_titles",user_id,self._repository.refresh_titles,user_id)
    async def equip_title(self,user_id,selector,request_id):return await self._run("equip_title",user_id,self._repository.equip_title,user_id,selector,request_id)
    async def remove_title(self,user_id,request_id):return await self._run("remove_title",user_id,self._repository.remove_title,user_id,request_id)
    async def get_active_events(self):return await self._run("get_active_events",0,self._repository.get_active_events)
    async def get_team(self,user_id):return await self._run("get_team",user_id,self._repository.get_team,user_id)
    async def invite_team_member(self,user_id,target_id,request_id):return await self._run("invite_team_member",user_id,self._repository.invite_team_member,user_id,target_id,request_id)
    async def resolve_team_invitation(self,user_id,invitation_id,action,request_id):return await self._run("resolve_team_invitation",user_id,self._repository.resolve_team_invitation,user_id,invitation_id,action,request_id)
    async def change_team(self,user_id,action,target_id,role,request_id):return await self._run("change_team",user_id,self._repository.change_team,user_id,action,target_id,role,request_id)
    async def get_economy_report(self):return await self._run("get_economy_report",0,self._repository.get_economy_report)
    async def get_tutorial(self,user_id):return await self._run("get_tutorial",user_id,self._repository.get_tutorial,user_id)
    async def update_tutorial(self,user_id,action,request_id):return await self._run("update_tutorial",user_id,self._repository.update_tutorial,user_id,action,request_id)
