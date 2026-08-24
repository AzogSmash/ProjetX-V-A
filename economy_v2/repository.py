from typing import Protocol

from db_bs import get_client

from economy_v2.models import IndustrialCompany, IndustrialUser


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
