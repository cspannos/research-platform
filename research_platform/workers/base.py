from __future__ import annotations

import redis
from rq import Queue

from research_platform.core.config import get_settings
from research_platform.core.tenancy import get_tenant, redis_url_for_tenant


def get_queue(tenant_id: str) -> Queue:
    tenant = get_tenant(tenant_id)
    settings = get_settings()
    url = redis_url_for_tenant(settings.redis_url, tenant)
    conn = redis.from_url(url)
    return Queue(tenant.queue_name, connection=conn)
