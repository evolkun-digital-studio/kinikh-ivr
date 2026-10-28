"""Department management endpoints (protected)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user_id, get_db
from app.models.department import Department
from app.services.call_service import CallService
from app.services.department_service import DepartmentService
from app.services.lead_service import LeadService

router = APIRouter(prefix="/departments", tags=["Departments"])


class DepartmentCreate(BaseModel):
    name: str
    display_name: str
    email: EmailStr
    greeting: str | None = None
    prompt: str | None = None
    knowledge_base: str | None = None
    keywords: list[str] | None = None


class DepartmentResponse(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    email: str
    greeting: str | None
    is_active: bool
    call_count: int = 0
    lead_count: int = 0

    model_config = {"from_attributes": True}

    @classmethod
    def from_department(
        cls, dept: Department, call_count: int = 0, lead_count: int = 0
    ) -> DepartmentResponse:
        return cls(
            id=dept.id,
            name=dept.name,
            display_name=dept.display_name,
            email=dept.email,
            greeting=dept.greeting,
            is_active=dept.is_active,
            call_count=call_count,
            lead_count=lead_count,
        )


@router.get("", response_model=list[DepartmentResponse])
async def list_departments(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> list[DepartmentResponse]:
    service = DepartmentService(db)
    depts = await service.list_all()

    lead_service = LeadService(db)
    call_service = CallService(db)
    lead_counts = await lead_service.counts_by_department()
    call_rows = await call_service.counts_by_department()
    call_counts = {dept_id: count for dept_id, _, count in call_rows}

    return [
        DepartmentResponse.from_department(
            d, call_count=call_counts.get(d.id, 0), lead_count=lead_counts.get(d.id, 0)
        )
        for d in depts
    ]


@router.post("", response_model=DepartmentResponse, status_code=201)
async def create_department(
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> DepartmentResponse:
    service = DepartmentService(db)
    dept = await service.create(**body.model_dump())
    return DepartmentResponse.from_department(dept)


@router.patch("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: uuid.UUID,
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> DepartmentResponse:
    service = DepartmentService(db)
    dept = await service.update(department_id, **body.model_dump(exclude_unset=True))
    return DepartmentResponse.from_department(dept)


@router.post("/{department_id}/activate", response_model=DepartmentResponse)
async def activate_department(
    department_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> DepartmentResponse:
    service = DepartmentService(db)
    dept = await service.activate(department_id)
    return DepartmentResponse.from_department(dept)


@router.delete("/{department_id}", status_code=204)
async def deactivate_department(
    department_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> None:
    service = DepartmentService(db)
    await service.deactivate(department_id)
