from typing import Protocol

from db_bs import get_client

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
    IngotShipment,
    ShipmentResult,
    Banker,
    WorldSale,
    DeliveryMission,
    DeliveryProfile,
    IndustrialContract,
)


class IndustrialEconomyRepository(Protocol):
    def get_or_create_player_actor(self, user_id: int) -> IndustrialActor: ...

    def get_or_create_user(self, user_id: int) -> IndustrialUser:
        ...

    def get_primary_company(self, user_id: int) -> IndustrialCompany | None:
        ...

    def create_first_company(
        self,
        user_id: int,
        name: str,
        job_type: str,
    ) -> tuple[str, IndustrialCompany | None]:
        ...

    def get_or_create_and_refresh_mine(
        self, user_id: int
    ) -> tuple[str, str | None, Mine | None]:
        ...

    def collect_mine(
        self, user_id: int
    ) -> tuple[str, str | None, MineCollectionResult | None]:
        ...

    def upgrade_mine(
        self, user_id: int, upgrade_type: str, request_id: str | None = None
    ) -> tuple[str, str | None, int | None, int | None, MineUpgradeResult | None]:
        ...

    def get_inventory(self, user_id: int) -> list[InventoryEntry]:
        ...

    def create_market_order(
        self,
        user_id: int,
        side: str,
        resource_type: str,
        quantity: int,
        unit_price: int,
        request_id: str,
    ) -> tuple[str, MarketOrderResult | None, int | None]:
        ...

    def cancel_market_order(
        self, user_id: int, order_id: int
    ) -> tuple[str, MarketOrder | None]:
        ...

    def get_market_orders(self, user_id: int) -> list[MarketOrder]:
        ...

    def get_market_summary(self, resource_type: str, depth: int) -> MarketSummary:
        ...

    def get_or_create_merchant(self, user_id: int) -> tuple[str, str | None, Merchant | None]:
        ...

    def upgrade_merchant(self, user_id: int, upgrade_type: str, request_id: str):
        ...

    def start_transport(self, user_id: int, receiver_user_id: int, resource_type: str,
                        quantity: int, request_id: str):
        ...

    def get_merchant_transports(self, user_id: int) -> tuple[str, str | None, list[IndustrialTransport]]:
        ...

    def get_or_create_blacksmith(self, user_id: int): ...
    def start_forge_job(self, user_id: int, resource_type: str, quantity: int, request_id: str): ...
    def collect_forge_jobs(self, user_id: int, request_id: str): ...
    def upgrade_forge(self, user_id: int, upgrade_type: str, request_id: str): ...
    def get_forge_jobs(self, user_id: int): ...
    def create_ingot_shipment(self, user_id: int, merchant_id: int, banker_id: int,
                              quantity: int, request_id: str): ...
    def cancel_ingot_shipment(self, user_id: int, shipment_id: int, request_id: str): ...
    def accept_ingot_shipment(self, user_id: int, shipment_id: int, request_id: str): ...
    def get_or_create_banker(self, user_id: int): ...
    def sell_world_ingots(self, user_id: int, quantity: int, request_id: str): ...
    def get_world_market(self): ...
    def get_world_sales(self, user_id: int): ...
    def get_delivery_missions(self): ...
    def get_delivery_profile(self, user_id: int): ...
    def accept_delivery(self, user_id: int, mission_id: int, request_id: str): ...
    def create_contract(self, user_id: int, resource: str, quantity: int, total: int, request_id: str): ...
    def accept_contract(self, user_id: int, contract_id: int, request_id: str): ...
    def cancel_contract(self, user_id: int, contract_id: int, request_id: str): ...
    def get_contracts(self, user_id: int, mine: bool): ...
    def record_activity(self, user_id: int): ...
    def evaluate_ai_companies(self): ...
    def purchase_ai_supply(self, user_id: int, resource_type: str,
                           quantity: int, request_id: str): ...
    def get_economy_stats(self): ...


def _user_from_row(row: dict) -> IndustrialUser:
    return IndustrialUser(
        discord_user_id=int(row["discord_user_id"]),
        credits=int(row["credits"]),
        primary_job=row.get("primary_job"),
    )


def _company_from_row(row: dict) -> IndustrialCompany:
    return IndustrialCompany(
        id=int(row["id"]),
        owner_discord_user_id=int(row["owner_discord_user_id"]),
        name=row["name"],
        job_type=row["job_type"],
        level=int(row["level"]),
        is_first_company=bool(row["is_first_company"]),
    )


def _mine_from_row(row: dict) -> Mine:
    return Mine(
        owner_discord_user_id=int(row["owner_discord_user_id"]),
        company_id=int(row["company_id"]),
        company_name=row["company_name"],
        resource_type=row["resource_type"],
        stock=int(row["stock"]),
        storage_level=int(row["storage_level"]),
        production_level=int(row["production_level"]),
        quality_level=int(row["quality_level"]),
        production_progress=int(row["production_progress"]),
        last_production_at=row["last_production_at"],
    )


def _market_order_from_row(row: dict) -> MarketOrder:
    return MarketOrder(
        id=int(row["id"]), owner_discord_user_id=int(row["owner_discord_user_id"]),
        side=row["side"], resource_type=row["resource_type"],
        original_quantity=int(row["original_quantity"]),
        remaining_quantity=int(row["remaining_quantity"]),
        unit_price=int(row["unit_price"]), status=row["status"],
        created_at=str(row["created_at"]),
    )


def _merchant_from_row(row: dict) -> Merchant:
    return Merchant(
        owner_discord_user_id=int(row["owner_discord_user_id"]),
        company_id=int(row["company_id"]), company_name=row["company_name"],
        truck_count=int(row["truck_count"]),
        truck_capacity_level=int(row["truck_capacity_level"]),
        truck_speed_level=int(row["truck_speed_level"]),
        warehouse_level=int(row["warehouse_level"]),
        active_transports=int(row["active_transports"]),
    )


def _transport_from_row(row: dict) -> IndustrialTransport:
    return IndustrialTransport(
        id=int(row["id"]), sender_company_id=int(row["sender_company_id"]),
        receiver_company_id=int(row["receiver_company_id"]),
        receiver_company_name=row["receiver_company_name"],
        merchant_discord_user_id=int(row["merchant_discord_user_id"]),
        resource_type=row["resource_type"], quantity=int(row["quantity"]),
        departure_at=str(row["departure_at"]), arrival_at=str(row["arrival_at"]),
        status=row["status"], truck_slot=int(row["truck_slot"]),
    )


def _blacksmith_from_row(row: dict) -> Blacksmith:
    return Blacksmith(
        owner_discord_user_id=int(row["owner_discord_user_id"]),
        company_id=int(row["company_id"]), company_name=row["company_name"],
        forge_level=int(row["forge_level"]), speed_level=int(row["speed_level"]),
        storage_level=int(row["storage_level"]), yield_level=int(row["yield_level"]),
        active_jobs=int(row["active_jobs"]), completed_jobs=int(row["completed_jobs"]),
        reserved_output=int(row["reserved_output"]),
    )


def _forge_job_from_row(row: dict) -> ForgeJob:
    return ForgeJob(
        id=int(row["id"]), owner_discord_user_id=int(row["owner_discord_user_id"]),
        company_id=int(row["company_id"]), forge_slot=int(row["forge_slot"]),
        resource_input=row["resource_input"], resource_output=row["resource_output"],
        input_quantity=int(row["input_quantity"]), output_quantity=int(row["output_quantity"]),
        started_at=str(row["started_at"]), finishes_at=str(row["finishes_at"]),
        status=row["status"],
    )


def _shipment_from_row(row: dict) -> IngotShipment:
    return IngotShipment(
        id=int(row["shipment_id"]),
        blacksmith_company_id=int(row["blacksmith_company_id"]),
        blacksmith_discord_user_id=int(row["blacksmith_discord_user_id"]),
        merchant_company_id=int(row["merchant_company_id"]),
        merchant_discord_user_id=int(row["merchant_discord_user_id"]),
        banker_company_id=int(row["banker_company_id"]),
        banker_discord_user_id=int(row["banker_discord_user_id"]),
        resource_type=row["resource_type"], quantity=int(row["quantity"]),
        status=row["status"], created_at=str(row["created_at"]),
        accepted_at=str(row["accepted_at"]) if row.get("accepted_at") else None,
        cancelled_at=str(row["cancelled_at"]) if row.get("cancelled_at") else None,
    )


class SupabaseIndustrialEconomyRepository:
    """Accès Supabase synchrone, exécuté hors event loop par le service async."""

    def get_or_create_player_actor(self, user_id: int) -> IndustrialActor:
        row = self._rpc_row("get_or_create_industrial_player_actor", {
            "p_discord_user_id": user_id})
        return IndustrialActor(int(row["id"]), row["actor_type"],
                               int(row["discord_user_id"]), None)

    def get_or_create_user(self, user_id: int) -> IndustrialUser:
        response = get_client().rpc(
            "get_or_create_industrial_user",
            {"p_discord_user_id": user_id},
        ).execute()
        if not response.data:
            raise RuntimeError("get_or_create_industrial_user returned no row")
        return _user_from_row(response.data[0])

    def get_primary_company(self, user_id: int) -> IndustrialCompany | None:
        response = (
            get_client()
            .table("industrial_companies")
            .select("id,owner_discord_user_id,name,job_type,level,is_first_company")
            .eq("owner_discord_user_id", user_id)
            .eq("is_first_company", True)
            .limit(1)
            .execute()
        )
        return _company_from_row(response.data[0]) if response.data else None

    def create_first_company(
        self,
        user_id: int,
        name: str,
        job_type: str,
    ) -> tuple[str, IndustrialCompany | None]:
        response = get_client().rpc(
            "create_first_industrial_company",
            {
                "p_owner_discord_user_id": user_id,
                "p_name": name,
                "p_job_type": job_type,
            },
        ).execute()
        if not response.data:
            raise RuntimeError("create_first_industrial_company returned no row")
        row = response.data[0]
        status = row["result_status"]
        if status == "already_exists":
            return status, None
        if status != "created":
            raise RuntimeError(f"unexpected create_first_company status: {status!r}")
        company = _company_from_row(row)
        if (
            company.owner_discord_user_id != user_id
            or company.job_type != job_type
            or not company.is_first_company
            or company.level < 1
        ):
            raise RuntimeError("inconsistent create_first_company result")
        return status, company

    @staticmethod
    def _rpc_row(function_name: str, parameters: dict) -> dict:
        response = get_client().rpc(function_name, parameters).execute()
        if not response.data:
            raise RuntimeError(f"{function_name} returned no row")
        return response.data[0]

    def get_or_create_and_refresh_mine(
        self, user_id: int
    ) -> tuple[str, str | None, Mine | None]:
        row = self._rpc_row(
            "get_or_create_and_refresh_industrial_mine",
            {"p_owner_discord_user_id": user_id},
        )
        status = row["result_status"]
        if status != "ok":
            return status, row.get("current_job"), None
        mine = _mine_from_row(row)
        if mine.owner_discord_user_id != user_id or mine.resource_type != "iron_ore":
            raise RuntimeError("inconsistent mine refresh result")
        return status, row.get("current_job"), mine

    def collect_mine(
        self, user_id: int
    ) -> tuple[str, str | None, MineCollectionResult | None]:
        row = self._rpc_row(
            "collect_industrial_mine",
            {"p_owner_discord_user_id": user_id},
        )
        status = row["result_status"]
        if status != "ok":
            return status, row.get("current_job"), None
        mine = _mine_from_row(row)
        inventory = InventoryEntry(
            owner_discord_user_id=user_id,
            resource_type=mine.resource_type,
            quantity=int(row["inventory_quantity"]),
        )
        result = MineCollectionResult(
            mine=mine,
            collected_quantity=int(row["collected_quantity"]),
            inventory=inventory,
        )
        return status, row.get("current_job"), result

    def upgrade_mine(
        self, user_id: int, upgrade_type: str, request_id: str | None = None
    ) -> tuple[str, str | None, int | None, int | None, MineUpgradeResult | None]:
        function_name = "upgrade_industrial_mine_idempotent" if request_id else "upgrade_industrial_mine"
        parameters = {"p_owner_discord_user_id": user_id, "p_upgrade_type": upgrade_type}
        if request_id: parameters["p_request_id"] = request_id
        row = self._rpc_row(function_name, parameters)
        status = row["result_status"]
        cost = int(row["upgrade_cost"]) if row.get("upgrade_cost") is not None else None
        balance = int(row["wallet_balance"]) if row.get("wallet_balance") is not None else None
        if status != "ok":
            return status, row.get("current_job"), cost, balance, None
        if cost is None or balance is None:
            raise RuntimeError("mine upgrade result is missing cost or balance")
        mine = _mine_from_row(row)
        result = MineUpgradeResult(
            mine=mine,
            upgrade_type=row["upgrade_type"],
            previous_level=int(row["previous_level"]),
            new_level=int(row["new_level"]),
            cost=cost,
            balance=balance,
        )
        return status, row.get("current_job"), cost, balance, result

    def get_inventory(self, user_id: int) -> list[InventoryEntry]:
        response = (
            get_client()
            .table("industrial_inventory")
            .select("owner_discord_user_id,resource_type,quantity")
            .eq("owner_discord_user_id", user_id)
            .execute()
        )
        return [
            InventoryEntry(
                owner_discord_user_id=int(row["owner_discord_user_id"]),
                resource_type=row["resource_type"],
                quantity=int(row["quantity"]),
            )
            for row in response.data
        ]

    def create_market_order(self, user_id: int, side: str, resource_type: str,
                            quantity: int, unit_price: int, request_id: str):
        row = self._rpc_row("create_industrial_market_order", {
            "p_owner_discord_user_id": user_id, "p_side": side,
            "p_resource_type": resource_type, "p_quantity": quantity,
            "p_unit_price": unit_price, "p_request_id": request_id,
        })
        status = row["result_status"]
        if status not in {"ok", "duplicate"}:
            return status, None, int(row["available_amount"]) if row.get("available_amount") is not None else None
        order = _market_order_from_row(row)
        return status, MarketOrderResult(order, int(row["filled_quantity"]), status == "duplicate"), None

    def cancel_market_order(self, user_id: int, order_id: int):
        row = self._rpc_row("cancel_industrial_market_order", {
            "p_owner_discord_user_id": user_id, "p_order_id": order_id,
        })
        return row["result_status"], (_market_order_from_row(row) if row.get("id") is not None else None)

    def get_market_orders(self, user_id: int) -> list[MarketOrder]:
        response = (get_client().table("industrial_market_orders")
                    .select("id,owner_discord_user_id,side,resource_type,original_quantity,remaining_quantity,unit_price,status,created_at")
                    .eq("owner_discord_user_id", user_id).eq("status", "open")
                    .order("created_at").execute())
        return [_market_order_from_row(row) for row in response.data]

    def get_market_summary(self, resource_type: str, depth: int) -> MarketSummary:
        client = get_client()
        stats = client.rpc("get_industrial_market_stats", {"p_resource_type": resource_type}).execute().data[0]
        fields = "id,owner_discord_user_id,side,resource_type,original_quantity,remaining_quantity,unit_price,status,created_at"
        sells = (client.table("industrial_market_orders").select(fields).eq("resource_type", resource_type)
                 .eq("side", "sell").eq("status", "open").order("unit_price").order("created_at").limit(depth).execute())
        buys = (client.table("industrial_market_orders").select(fields).eq("resource_type", resource_type)
                .eq("side", "buy").eq("status", "open").order("unit_price", desc=True).order("created_at").limit(depth).execute())
        return MarketSummary(resource_type,
            float(stats["average_price_24h"]) if stats["average_price_24h"] is not None else None,
            int(stats["low_price_24h"]) if stats["low_price_24h"] is not None else None,
            int(stats["high_price_24h"]) if stats["high_price_24h"] is not None else None,
            int(stats["volume_24h"]), tuple(map(_market_order_from_row, sells.data)),
            tuple(map(_market_order_from_row, buys.data)))

    def get_or_create_merchant(self, user_id: int):
        row = self._rpc_row("get_or_create_and_refresh_industrial_merchant", {
            "p_owner_discord_user_id": user_id,
        })
        status = row["result_status"]
        return status, row.get("current_job"), (_merchant_from_row(row) if status == "ok" else None)

    def upgrade_merchant(self, user_id: int, upgrade_type: str, request_id: str):
        row = self._rpc_row("upgrade_industrial_merchant", {
            "p_owner_discord_user_id": user_id,
            "p_upgrade_type": upgrade_type,
            "p_request_id": request_id,
        })
        status = row["result_status"]
        if status not in {"ok", "duplicate"}:
            cost = int(row["upgrade_cost"]) if row.get("upgrade_cost") is not None else None
            balance = int(row["wallet_balance"]) if row.get("wallet_balance") is not None else None
            return status, row.get("current_job"), cost, balance, None
        merchant = _merchant_from_row(row)
        result = MerchantUpgradeResult(
            merchant, row["upgrade_type"], int(row["previous_level"]),
            int(row["new_level"]), int(row["upgrade_cost"]),
            int(row["wallet_balance"]), status == "duplicate",
        )
        return status, row.get("current_job"), result.cost, result.balance, result

    def start_transport(self, user_id: int, receiver_user_id: int, resource_type: str,
                        quantity: int, request_id: str):
        row = self._rpc_row("start_industrial_merchant_transport_with_delivery", {
            "p_merchant_discord_user_id": user_id,
            "p_receiver_discord_user_id": receiver_user_id,
            "p_resource_type": resource_type,
            "p_quantity": quantity,
            "p_request_id": request_id,
        })
        status = row["result_status"]
        available = int(row["available_amount"]) if row.get("available_amount") is not None else None
        if status not in {"ok", "duplicate"}:
            return status, row.get("current_job"), available, None
        return status, row.get("current_job"), available, MerchantTransportResult(
            _transport_from_row(row), status == "duplicate"
        )

    def get_merchant_transports(self, user_id: int):
        status, current_job, merchant = self.get_or_create_merchant(user_id)
        if status != "ok" or merchant is None:
            return status, current_job, []
        response = (get_client().table("industrial_transports")
                    .select("id,sender_company_id,receiver_company_id,merchant_discord_user_id,resource_type,quantity,departure_at,arrival_at,status,truck_slot,industrial_companies!industrial_transports_receiver_company_id_fkey(name)")
                    .eq("merchant_discord_user_id", user_id).order("created_at", desc=True).limit(20).execute())
        rows = []
        for row in response.data:
            row["receiver_company_name"] = row.pop("industrial_companies")["name"]
            rows.append(_transport_from_row(row))
        return status, current_job, rows

    def get_or_create_blacksmith(self, user_id: int):
        row = self._rpc_row("get_or_create_and_refresh_industrial_blacksmith", {
            "p_owner_discord_user_id": user_id,
        })
        status = row["result_status"]
        return status, row.get("current_job"), (_blacksmith_from_row(row) if status == "ok" else None)

    def start_forge_job(self, user_id: int, resource_type: str,
                        quantity: int, request_id: str):
        row = self._rpc_row("start_industrial_forge_job", {
            "p_owner_discord_user_id": user_id, "p_resource_type": resource_type,
            "p_quantity": quantity, "p_request_id": request_id,
        })
        status = row["result_status"]
        available = int(row["available_amount"]) if row.get("available_amount") is not None else None
        if status not in {"ok", "duplicate"}:
            return status, row.get("current_job"), available, None
        return status, row.get("current_job"), available, ForgeProcessResult(
            _forge_job_from_row(row), int(row["remaining_input"]), status == "duplicate")

    def collect_forge_jobs(self, user_id: int, request_id: str):
        row = self._rpc_row("collect_industrial_forge_jobs", {
            "p_owner_discord_user_id": user_id, "p_request_id": request_id,
        })
        status = row["result_status"]
        if status not in {"ok", "duplicate"}:
            return status, row.get("current_job"), None
        return status, row.get("current_job"), ForgeCollectionResult(
            int(row["collected_quantity"]), int(row["inventory_quantity"]),
            status == "duplicate")

    def upgrade_forge(self, user_id: int, upgrade_type: str, request_id: str):
        row = self._rpc_row("upgrade_industrial_forge", {
            "p_owner_discord_user_id": user_id, "p_upgrade_type": upgrade_type,
            "p_request_id": request_id,
        })
        status = row["result_status"]
        cost = int(row["upgrade_cost"]) if row.get("upgrade_cost") is not None else None
        balance = int(row["wallet_balance"]) if row.get("wallet_balance") is not None else None
        if status not in {"ok", "duplicate"}:
            return status, row.get("current_job"), cost, balance, None
        result = ForgeUpgradeResult(
            _blacksmith_from_row(row), row["upgrade_type"],
            int(row["previous_level"]), int(row["new_level"]),
            int(row["upgrade_cost"]), int(row["wallet_balance"]),
            status == "duplicate",
        )
        return status, row.get("current_job"), result.cost, result.balance, result

    def get_forge_jobs(self, user_id: int):
        status, current_job, blacksmith = self.get_or_create_blacksmith(user_id)
        if status != "ok" or blacksmith is None:
            return status, current_job, []
        response = (get_client().table("industrial_forge_jobs")
                    .select("id,owner_discord_user_id,company_id,forge_slot,resource_input,resource_output,input_quantity,output_quantity,started_at,finishes_at,status")
                    .eq("owner_discord_user_id", user_id).order("started_at", desc=True).limit(20).execute())
        return status, current_job, [_forge_job_from_row(row) for row in response.data]

    def create_ingot_shipment(self, user_id: int, merchant_id: int, banker_id: int,
                              quantity: int, request_id: str):
        row = self._rpc_row("create_industrial_ingot_shipment", {
            "p_blacksmith_discord_user_id": user_id,
            "p_merchant_discord_user_id": merchant_id,
            "p_banker_discord_user_id": banker_id,
            "p_quantity": quantity, "p_request_id": request_id,
        })
        status = row["result_status"]
        available = int(row["available_amount"]) if row.get("available_amount") is not None else None
        result = ShipmentResult(_shipment_from_row(row), duplicate_request=status == "duplicate") \
            if status in {"ok", "duplicate"} else None
        return status, row.get("current_job"), available, result

    def cancel_ingot_shipment(self, user_id: int, shipment_id: int, request_id: str):
        row = self._rpc_row("cancel_industrial_ingot_shipment", {
            "p_blacksmith_discord_user_id": user_id,
            "p_shipment_id": shipment_id, "p_request_id": request_id,
        })
        status = row["result_status"]
        result = ShipmentResult(_shipment_from_row(row), duplicate_request=status == "duplicate") \
            if status in {"ok", "duplicate"} else None
        return status, row.get("current_job"), result

    def accept_ingot_shipment(self, user_id: int, shipment_id: int, request_id: str):
        row = self._rpc_row("accept_industrial_ingot_shipment_with_delivery", {
            "p_merchant_discord_user_id": user_id,
            "p_shipment_id": shipment_id, "p_request_id": request_id,
        })
        status = row["result_status"]
        available = int(row["available_amount"]) if row.get("available_amount") is not None else None
        if status not in {"ok", "duplicate"}:
            return status, row.get("current_job"), available, None
        transport = None
        if row.get("id") is not None:
            transport_row = dict(row)
            transport_row.update({
                "merchant_discord_user_id": user_id,
                "resource_type": row["resource_type"],
                "quantity": row["quantity"],
                "status": "in_transit",
            })
            transport = _transport_from_row(transport_row)
        return status, row.get("current_job"), available, ShipmentResult(
            _shipment_from_row(row), transport, status == "duplicate")

    def get_or_create_banker(self, user_id: int):
        row = self._rpc_row("get_or_create_and_refresh_industrial_banker", {
            "p_owner_discord_user_id": user_id})
        if row["result_status"] != "ok":
            return row["result_status"], row.get("current_job"), None
        return "ok", row.get("current_job"), Banker(
            user_id, int(row["company_id"]), row["company_name"], int(row["credits"]))

    def sell_world_ingots(self, user_id: int, quantity: int, request_id: str):
        row = self._rpc_row("sell_industrial_ingots_to_world", {
            "p_banker_discord_user_id": user_id, "p_quantity": quantity,
            "p_request_id": request_id})
        status = row["result_status"]
        available = int(row["available_amount"]) if row.get("available_amount") is not None else None
        if status not in {"ok", "duplicate"}:
            return status, row.get("current_job"), available, None
        return status, row.get("current_job"), available, WorldSale(
            int(row["sale_id"]), int(row["quantity"]), int(row["unit_price"]),
            int(row["total_credits"]), int(row["balance_after"]), str(row["created_at"]),
            status == "duplicate")

    def get_world_market(self):
        return self._rpc_row("get_industrial_world_market", {"p_resource_type": "iron_ingot"})

    def get_world_sales(self, user_id: int):
        response = (get_client().table("industrial_world_sales")
                    .select("id,quantity,unit_price,total_credits,balance_after,created_at")
                    .eq("banker_discord_user_id", user_id).order("created_at", desc=True)
                    .limit(20).execute())
        return [WorldSale(int(r["id"]), int(r["quantity"]), int(r["unit_price"]),
                          int(r["total_credits"]), int(r["balance_after"]), str(r["created_at"]))
                for r in response.data]

    def get_delivery_missions(self):
        response = (get_client().table("industrial_delivery_missions")
                    .select("id,transport_id,merchant_discord_user_id,merchant_actor_id,resource_type,quantity,status,commission_max,industrial_transports!industrial_delivery_missions_transport_id_fkey(arrival_at)")
                    .eq("status", "open").order("created_at").limit(20).execute())
        return [DeliveryMission(int(r["id"]), int(r["transport_id"]),
            int(r["merchant_discord_user_id"]) if r.get("merchant_discord_user_id") else None,
            int(r["merchant_actor_id"]), r["resource_type"], int(r["quantity"]),
            r["status"], int(r["commission_max"]), str(r["industrial_transports"]["arrival_at"]))
            for r in response.data]

    def get_delivery_profile(self, user_id: int):
        row = self._rpc_row("get_industrial_delivery_profile", {"p_discord_user_id": user_id})
        return DeliveryProfile(user_id, int(row["delivery_level"]), int(row["delivery_xp"]),
                               int(row["completed_deliveries"]), row.get("delivery_cooldown_until"))

    def accept_delivery(self, user_id: int, mission_id: int, request_id: str):
        return self._rpc_row("accept_industrial_delivery", {
            "p_courier_discord_user_id": user_id, "p_mission_id": mission_id,
            "p_request_id": request_id})

    @staticmethod
    def _contract_from_row(row: dict) -> IndustrialContract:
        return IndustrialContract(int(row["id"]), int(row["creator_discord_user_id"]),
            int(row["accepter_discord_user_id"]) if row.get("accepter_discord_user_id") else None,
            row["resource_type"], int(row["quantity"]), int(row["total_price"]),
            row["status"], str(row["expires_at"]))

    def create_contract(self, user_id: int, resource: str, quantity: int, total: int, request_id: str):
        row = self._rpc_row("create_industrial_resource_contract", {
            "p_creator_discord_user_id": user_id, "p_resource_type": resource,
            "p_quantity": quantity, "p_total_price": total, "p_request_id": request_id})
        return row["result_status"], row.get("available_amount"), \
            (self._contract_from_row(row) if row.get("id") else None)

    def accept_contract(self, user_id: int, contract_id: int, request_id: str):
        row = self._rpc_row("accept_industrial_resource_contract", {
            "p_accepter_discord_user_id": user_id, "p_contract_id": contract_id,
            "p_request_id": request_id})
        return row["result_status"], row.get("available_amount"), \
            (self._contract_from_row(row) if row.get("id") else None)

    def cancel_contract(self, user_id: int, contract_id: int, request_id: str):
        row = self._rpc_row("cancel_industrial_resource_contract", {
            "p_creator_discord_user_id": user_id, "p_contract_id": contract_id,
            "p_request_id": request_id})
        return row["result_status"], (self._contract_from_row(row) if row.get("id") else None)

    def get_contracts(self, user_id: int, mine: bool):
        response = get_client().rpc("get_industrial_resource_contracts", {
            "p_discord_user_id": user_id, "p_mine": mine}).execute()
        return [self._contract_from_row(r) for r in response.data]

    def record_activity(self, user_id: int):
        self._rpc_row("record_industrial_activity", {"p_discord_user_id": user_id})

    def evaluate_ai_companies(self):
        response = get_client().rpc("evaluate_industrial_ai_companies", {}).execute()
        return response.data

    def purchase_ai_supply(self, user_id: int, resource_type: str,
                           quantity: int, request_id: str):
        return self._rpc_row("purchase_industrial_ai_supply", {
            "p_buyer_discord_user_id": user_id, "p_resource_type": resource_type,
            "p_quantity": quantity, "p_request_id": request_id})

    def get_economy_stats(self):
        get_client().rpc("ensure_industrial_ai_economy", {}).execute()
        return self._rpc_row("get_industrial_actor_economy_stats", {})
