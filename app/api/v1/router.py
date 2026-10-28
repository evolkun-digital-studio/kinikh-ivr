"""Aggregates all v1 API routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    auth,
    calls,
    dashboard,
    departments,
    emails,
    health,
    leads,
    recordings,
    system,
    telephony,
    usage,
)

router = APIRouter(prefix="/api/v1")

router.include_router(health.router)
router.include_router(auth.router)
router.include_router(leads.router)
router.include_router(departments.router)
router.include_router(telephony.router)
router.include_router(calls.router)
router.include_router(recordings.router)
router.include_router(emails.router)
router.include_router(usage.router)
router.include_router(analytics.router)
router.include_router(dashboard.router)
router.include_router(system.router)
