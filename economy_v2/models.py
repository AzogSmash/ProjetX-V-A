from dataclasses import dataclass


@dataclass(frozen=True)
class IndustrialUser:
    discord_user_id: int
    credits: int
    primary_job: str | None


@dataclass(frozen=True)
class IndustrialCompany:
    id: int
    owner_discord_user_id: int
    name: str
    job_type: str
    level: int
    is_first_company: bool
