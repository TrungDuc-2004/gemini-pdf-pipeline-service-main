from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_assets,
    routes_bundle,
    routes_chunks,
    routes_debug,
    routes_health,
    routes_jobs,
    routes_lessons,
    routes_logs,
    routes_mongo_import,
    routes_sync,
    routes_topics,
)
from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Standalone review-first PDF pipeline service.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_health.router)
app.include_router(routes_debug.router)
app.include_router(routes_assets.router)
app.include_router(routes_jobs.router)
app.include_router(routes_logs.router)
app.include_router(routes_topics.router)
app.include_router(routes_lessons.router)
app.include_router(routes_chunks.router)
app.include_router(routes_bundle.router)
app.include_router(routes_mongo_import.router)
app.include_router(routes_sync.router)
