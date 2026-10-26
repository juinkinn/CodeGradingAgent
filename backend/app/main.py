from contextlib import asynccontextmanager
import logging
import time
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.database import init_db
from app.env import settings
from app.telemetry import configure_grafana_otel, shutdown_grafana_otel


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    telemetry_providers = configure_grafana_otel(app)
    try:
        yield
    finally:
        shutdown_grafana_otel(telemetry_providers)


app = FastAPI(title="CodeGradingAgent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_submission_upload_duration(request: Request, call_next):
    if (
        request.method != "POST"
        or not request.url.path.endswith("/submissions/upload")
    ):
        return await call_next(request)

    start_time = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        logger.info(
            "Submission upload request completed: path=%s status_code=%s duration_ms=%.2f",
            request.url.path,
            status_code,
            (time.perf_counter() - start_time) * 1000,
        )


app.include_router(api_router)
