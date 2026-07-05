from research_platform.core.tenancy import TENANTS, TenantId, get_tenant


def test_tenant_registry_has_all_projects() -> None:
    assert set(TENANTS) == {
        TenantId.DEMO,
        TenantId.MEV,
        TenantId.ANOMALY,
        TenantId.COLLECTIVE,
        TenantId.EXOPLANET,
    }


def test_get_tenant_returns_redis_db() -> None:
    tenant = get_tenant("mev")
    assert tenant.redis_db == 1
    assert tenant.database_name == "tenant_mev"
