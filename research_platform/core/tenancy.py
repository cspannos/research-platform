from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TenantId(str, Enum):
    DEMO = "demo"
    MEV = "mev"
    ANOMALY = "anomaly"
    COLLECTIVE = "collective"
    EXOPLANET = "exoplanet"


@dataclass(frozen=True)
class TenantSpec:
    tenant_id: TenantId
    display_name: str
    redis_db: int
    queue_name: str
    database_name: str
    description: str


TENANTS: dict[TenantId, TenantSpec] = {
    TenantId.DEMO: TenantSpec(
        tenant_id=TenantId.DEMO,
        display_name="Demo / Platform Smoke Test",
        redis_db=0,
        queue_name="demo",
        database_name="tenant_demo",
        description="Minimal bot + worker stub for platform validation.",
    ),
    TenantId.MEV: TenantSpec(
        tenant_id=TenantId.MEV,
        display_name="MEV Research",
        redis_db=1,
        queue_name="mev",
        database_name="tenant_mev",
        description="MEV pattern monitoring and Telegram summaries.",
    ),
    TenantId.ANOMALY: TenantSpec(
        tenant_id=TenantId.ANOMALY,
        display_name="Blockchain Anomaly Detection",
        redis_db=2,
        queue_name="anomaly",
        database_name="tenant_anomaly",
        description="Unusual account movement and large fund flow alerts.",
    ),
    TenantId.COLLECTIVE: TenantSpec(
        tenant_id=TenantId.COLLECTIVE,
        display_name="AI Anarchist Collective",
        redis_db=3,
        queue_name="collective",
        database_name="tenant_collective",
        description="Lightweight publishing and coordination via Telegram.",
    ),
    TenantId.EXOPLANET: TenantSpec(
        tenant_id=TenantId.EXOPLANET,
        display_name="Exoplanet Citizen Science",
        redis_db=4,
        queue_name="exoplanet",
        database_name="tenant_exoplanet",
        description="Targeted TESS/Kepler light-curve analysis and review summaries.",
    ),
}


def get_tenant(tenant_id: str) -> TenantSpec:
    try:
        return TENANTS[TenantId(tenant_id)]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unknown tenant: {tenant_id}") from exc


def redis_url_for_tenant(base_redis_url: str, tenant: TenantSpec) -> str:
    separator = "&" if "?" in base_redis_url else "?"
    return f"{base_redis_url}{separator}db={tenant.redis_db}"
