from __future__ import annotations

from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.workers.base import get_queue
from research_platform.workers.jobs.demo import ping_job

logger = get_logger(__name__)


def enqueue_heartbeat() -> None:
    queue = get_queue("demo")
    job = queue.enqueue(ping_job, message=f"scheduled-heartbeat-{datetime.now(UTC).isoformat()}")
    logger.info("scheduled_job_enqueued", job_id=job.id, queue="demo")


def main() -> None:
    settings = get_settings()
    configure_logging("scheduler", settings.tenant_id)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        enqueue_heartbeat,
        trigger=CronTrigger(minute="*/15"),
        id="demo-heartbeat",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    logger.info("scheduler_started", jobs=["demo-heartbeat"])
    scheduler.start()


if __name__ == "__main__":
    main()
