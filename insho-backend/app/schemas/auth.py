from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=32, max_length=200)
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: UUID
    full_name: str
    email: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse


class InvitationAcceptedResponse(BaseModel):
    organization_id: UUID
    role: str
    user: UserResponse
    access_token: str
    token_type: str
    expires_at: datetime
