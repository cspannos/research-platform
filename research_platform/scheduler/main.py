from __future__ import annotations

import os
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.workers.base import get_queue
from research_platform.workers.jobs.demo import ping_job

logger = get_logger(__name__)


def _profile_enabled(name: str) -> bool:
    profiles = os.getenv("ENABLE_SCHEDULER_PROFILES", "demo,exoplanet")
    return name in {p.strip() for p in profiles.split(",") if p.strip()}


def enqueue_demo_heartbeat() -> None:
    queue = get_queue("demo")
    job = queue.enqueue(ping_job, message=f"scheduled-heartbeat-{datetime.now(timezone.utc).isoformat()}")
    logger.info("scheduled_job_enqueued", job_id=job.id, queue="demo", job="heartbeat")


def enqueue_exoplanet_ingest() -> None:
    from projects.exoplanet.workers.jobs import exoplanet_ingest_job

    queue = get_queue("exoplanet")
    job = queue.enqueue(exoplanet_ingest_job)
    logger.info("scheduled_job_enqueued", job_id=job.id, queue="exoplanet", job="ingest")


def enqueue_exoplanet_scan() -> None:
    from projects.exoplanet.workers.jobs import exoplanet_scan_job

    queue = get_queue("exoplanet")
    job = queue.enqueue(exoplanet_scan_job)
    logger.info("scheduled_job_enqueued", job_id=job.id, queue="exoplanet", job="scan")


def enqueue_exoplanet_review() -> None:
    from projects.exoplanet.workers.jobs import (
        exoplanet_notify_telegram_job,
        exoplanet_review_summary_job,
    )

    queue = get_queue("exoplanet")
    summary_job = queue.enqueue(exoplanet_review_summary_job)
    notify_job = queue.enqueue(exoplanet_notify_telegram_job, depends_on=summary_job)
    logger.info(
        "scheduled_jobs_enqueued",
        queue="exoplanet",
        summary_job_id=summary_job.id,
        notify_job_id=notify_job.id,
    )


def main() -> None:
    settings = get_settings()
    configure_logging("scheduler", settings.tenant_id)

    scheduler = BlockingScheduler(timezone="UTC")
    jobs = []

    if _profile_enabled("demo"):
        scheduler.add_job(
            enqueue_demo_heartbeat,
            trigger=CronTrigger(minute="*/15"),
            id="demo-heartbeat",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        jobs.append("demo-heartbeat")

    if _profile_enabled("exoplanet"):
        scheduler.add_job(
            enqueue_exoplanet_ingest,
            trigger=CronTrigger(hour=2, minute=0),
            id="exoplanet-ingest",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            enqueue_exoplanet_scan,
            trigger=CronTrigger(hour=4, minute=0),
            id="exoplanet-scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            enqueue_exoplanet_review,
            trigger=CronTrigger(hour=10, minute=0),
            id="exoplanet-review",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        jobs.extend(["exoplanet-ingest", "exoplanet-scan", "exoplanet-review"])

    logger.info("scheduler_started", jobs=jobs)
    scheduler.start()


if __name__ == "__main__":
    main()
