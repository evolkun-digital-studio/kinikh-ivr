"""AuditLog-specific data access operations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AuditLog, session)

    async def log(
        self,
        action: str,
        actor: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        extra_data: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        return await self.create(
            action=action,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            extra_data=extra_data,
            ip_address=ip_address,
        )

    async def list_by_resource(self, resource_type: str, resource_id: str) -> Sequence[AuditLog]:
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.resource_type == resource_type, AuditLog.resource_id == resource_id)
            .order_by(AuditLog.created_at.desc())
        )
        return result.scalars().all()
