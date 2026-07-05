from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(service_name: str, tenant_id: str = "platform") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    structlog.contextvars.bind_contextvars(service=service_name, tenant=tenant_id)


def get_logger(name: str):
    return structlog.get_logger(name)
