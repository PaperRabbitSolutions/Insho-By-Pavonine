from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import EMAIL_PATTERN


class InvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    role: str = Field(pattern=r"^(admin|designer|member)$")


class InvitationResponse(BaseModel):
    id: UUID
    organization_id: UUID
    email: str
    role: str
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvitationCreatedResponse(InvitationResponse):
    token: str


class OrganizationMemberResponse(BaseModel):
    user_id: UUID
    email: str
    full_name: str
    role: str
    created_at: datetime


class OrganizationMemberUpdate(BaseModel):
    role: str = Field(pattern=r"^(owner|admin|designer|member)$")


class MembershipRemovedResponse(BaseModel):
    removed: bool
