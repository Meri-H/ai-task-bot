"""FastAPI application factory.  Run with:  uvicorn app.api.main:app"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_health, routes_tasks, ws
from app.api.hub import EventHub
from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_session_factory
from app.core.logging import configure_logging
from app.core.queue import create_queue_pool

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        if not settings.dashboard_token:
            logger.warning("DASHBOARD_TOKEN is empty: the API and dashboard are NOT authenticated")

        engine = create_engine(settings.database_url, pool_size=10, max_overflow=10)
        redis = await create_queue_pool(settings.redis_url)
        hub = EventHub(redis)
        await hub.start()

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.hub = hub
        try:
            yield
        finally:
            await hub.stop()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="AI Task Bot API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    api = APIRouter(prefix="/api")
    api.include_router(routes_health.router)
    api.include_router(routes_tasks.router)
    api.include_router(ws.router)
    app.include_router(api)
    return app


app = create_app()
