import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.onboarding import OnboardingRequest
from not_dot_net.backend.users import current_active_user

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class OnboardingCreate(BaseModel):
    person_name: str
    person_email: EmailStr
    role_status: str
    team: str
    start_date: date
    note: Optional[str] = None


class OnboardingRead(BaseModel):
    id: uuid.UUID
    created_by: Optional[uuid.UUID]
    person_name: str
    person_email: str
    role_status: str
    team: str
    start_date: date
    note: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=OnboardingRead, status_code=201)
async def create_onboarding_request(
    data: OnboardingCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    request = OnboardingRequest(
        created_by=user.id,
        person_name=data.person_name,
        person_email=data.person_email,
        role_status=data.role_status,
        team=data.team,
        start_date=data.start_date,
        note=data.note,
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request


@router.get("", response_model=list[OnboardingRead])
async def list_onboarding_requests(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    if user.is_superuser:
        stmt = select(OnboardingRequest).order_by(OnboardingRequest.created_at.desc())
    else:
        stmt = (
            select(OnboardingRequest)
            .where(OnboardingRequest.created_by == user.id)
            .order_by(OnboardingRequest.created_at.desc())
        )
    result = await session.execute(stmt)
    return result.scalars().all()
