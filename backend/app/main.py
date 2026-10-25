from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.database import init_db
from app.env import settings
from app.telemetry import configure_grafana_otel, shutdown_grafana_otel


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
app.include_router(api_router)
