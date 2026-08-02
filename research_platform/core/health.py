"""Shared health-check helpers for container probes."""

from __future__ import annotations

import argparse
import sys

import redis
from rq import Worker

from research_platform.core.config import get_settings
from research_platform.core.tenancy import get_tenant, redis_url_for_tenant


def check_redis() -> None:
    settings = get_settings()
    tenant = get_tenant(settings.tenant_id)
    url = redis_url_for_tenant(settings.redis_url, tenant)
    client = redis.from_url(url)
    if not client.ping():
        raise RuntimeError("Redis ping failed")


def check_worker() -> None:
    check_redis()
    settings = get_settings()
    tenant = get_tenant(settings.tenant_id)
    url = redis_url_for_tenant(settings.redis_url, tenant)
    workers = Worker.all(connection=redis.from_url(url))
    if not workers:
        raise RuntimeError(f"No active RQ workers for tenant={tenant.tenant_id}")


def check_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    check_redis()


def check_scheduler() -> None:
    # Scheduler is platform-wide (TENANT_ID=platform); ping shared Redis only.
    settings = get_settings()
    client = redis.from_url(settings.redis_url)
    if not client.ping():
        raise RuntimeError("Redis ping failed")


CHECKS = {
    "redis": check_redis,
    "worker": check_worker,
    "bot": check_bot,
    "scheduler": check_scheduler,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=sorted(CHECKS))
    args = parser.parse_args()
    CHECKS[args.target]()
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - health probe
        print(f"unhealthy: {exc}", file=sys.stderr)
        sys.exit(1)
