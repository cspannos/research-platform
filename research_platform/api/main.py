from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from starlette.responses import PlainTextResponse, Response

from research_platform.api.exoplanet import router as exoplanet_router
from research_platform.api.review_dashboard import review_root_redirect, router as review_router
from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.core.tenancy import TENANTS

configure_logging("platform-api", "platform")
logger = get_logger(__name__)

REQUESTS = Counter("platform_http_requests_total", "HTTP requests", ["path", "method", "status"])

app = FastAPI(title="Research Platform API", version="0.1.0")
app.include_router(exoplanet_router)
app.include_router(review_router)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    response = await call_next(request)
    REQUESTS.labels(request.url.path, request.method, response.status_code).inc()
    return response


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    settings = get_settings()
    try:
        import redis

        client = redis.from_url(settings.redis_url)
        client.ping()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return {"status": "ready"}


@app.get("/tenants")
async def list_tenants() -> list[dict[str, str]]:
    return [
        {
            "tenant_id": spec.tenant_id,
            "display_name": spec.display_name,
            "queue": spec.queue_name,
            "database": spec.database_name,
            "description": spec.description,
        }
        for spec in TENANTS.values()
    ]


@app.get("/metrics")
async def metrics() -> Response:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root(request: Request):
    host = request.headers.get("host", "")
    if host.startswith("review."):
        return review_root_redirect(request)
    return {"service": "research-platform-api", "version": "0.1.0"}
