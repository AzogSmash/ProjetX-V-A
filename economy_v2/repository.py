from typing import Protocol

from db_bs import get_client

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


class IndustrialEconomyRepository(Protocol):
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
        self, user_id: int, upgrade_type: str
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


class SupabaseIndustrialEconomyRepository:
    """Accès Supabase synchrone, exécuté hors event loop par le service async."""

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
        self, user_id: int, upgrade_type: str
    ) -> tuple[str, str | None, int | None, int | None, MineUpgradeResult | None]:
        row = self._rpc_row(
            "upgrade_industrial_mine",
            {
                "p_owner_discord_user_id": user_id,
                "p_upgrade_type": upgrade_type,
            },
        )
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
