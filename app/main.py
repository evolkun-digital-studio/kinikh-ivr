"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
)
from app.core.logger import configure_logging
from app.core.middleware import RequestLoggingMiddleware, SecurityHeadersMiddleware
from app.db.session import close_engine, get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.app_env, settings.app_debug)
    logger.info(f"Starting {settings.app_name} [{settings.app_env}]")

    engine = get_engine()
    async with engine.connect() as conn:
        from sqlalchemy import text

        await conn.execute(text("SELECT 1"))
    logger.info("Database connection pool ready")

    # Pre-synthesize the greeting so the first caller hears it instantly.
    import asyncio

    from app.voice.speech_handler import prewarm_fillers, prewarm_greeting

    prewarm_task = asyncio.create_task(prewarm_greeting())
    filler_prewarm_task = asyncio.create_task(prewarm_fillers())
    
    from app.rag.index import get_knowledge_index
    
    async def build_index_safe():
        try:
            await get_knowledge_index().build()
        except Exception as exc:
            logger.warning(f"[RAG] Failed to build knowledge index: {exc}")
            
    rag_task = asyncio.create_task(build_index_safe())

    yield

    prewarm_task.cancel()
    filler_prewarm_task.cancel()
    rag_task.cancel()

    await close_engine()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="AI Voice Receptionist — captures leads from inbound calls.",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ────────────────────────────────────────────────────
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    return app


app = create_app()
