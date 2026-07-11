from __future__ import annotations

import argparse
import sys

import redis
from rq import Queue, Worker

from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.core.tenancy import get_tenant, redis_url_for_tenant

TENANT_JOB_MODULES: dict[str, list[str]] = {
    "demo": ["research_platform.workers.jobs"],
    "collective": ["projects.collective.workers.jobs"],
    "exoplanet": ["projects.exoplanet.workers.jobs"],
}


def import_tenant_jobs(tenant_id: str) -> None:
    import importlib

    for module_name in TENANT_JOB_MODULES.get(tenant_id, ["research_platform.workers.jobs"]):
        importlib.import_module(module_name)

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    args = parser.parse_args()

    tenant = get_tenant(args.tenant)
    settings = get_settings()
    configure_logging(f"worker-{tenant.tenant_id}", tenant.tenant_id)
    import_tenant_jobs(tenant.tenant_id)

    url = redis_url_for_tenant(settings.redis_url, tenant)
    conn = redis.from_url(url)
    queue = Queue(tenant.queue_name, connection=conn)

    logger.info("worker_starting", queue=tenant.queue_name, redis_db=tenant.redis_db)
    worker = Worker([queue], connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
