from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DesignDNACreate(BaseModel):
    project_id: UUID

    palette: Optional[Dict[str, Any]] = None
    materials: Optional[Dict[str, Any]] = None
    floral: Optional[Dict[str, Any]] = None
    lighting: Optional[Dict[str, Any]] = None
    architecture: Optional[Dict[str, Any]] = None
    motifs: Optional[Dict[str, Any]] = None
    cultural_inspiration: Optional[Dict[str, Any]] = None

    mood: Optional[str] = Field(default=None, max_length=150)
    formality: Optional[str] = Field(default=None, max_length=100)

    luxury: Optional[Decimal] = None
    confidence: Optional[Decimal] = None


class DesignDNAUpdate(BaseModel):
    palette: Optional[Dict[str, Any]] = None
    materials: Optional[Dict[str, Any]] = None
    floral: Optional[Dict[str, Any]] = None
    lighting: Optional[Dict[str, Any]] = None
    architecture: Optional[Dict[str, Any]] = None
    motifs: Optional[Dict[str, Any]] = None
    cultural_inspiration: Optional[Dict[str, Any]] = None

    mood: Optional[str] = Field(default=None, max_length=150)
    formality: Optional[str] = Field(default=None, max_length=100)

    luxury: Optional[Decimal] = None
    confidence: Optional[Decimal] = None


class DesignDNAResponse(BaseModel):
    id: UUID
    project_id: UUID

    palette: Optional[Dict[str, Any]]
    materials: Optional[Dict[str, Any]]
    floral: Optional[Dict[str, Any]]
    lighting: Optional[Dict[str, Any]]
    architecture: Optional[Dict[str, Any]]
    motifs: Optional[Dict[str, Any]]
    cultural_inspiration: Optional[Dict[str, Any]]

    mood: Optional[str]
    formality: Optional[str]
    luxury: Optional[Decimal]
    confidence: Optional[Decimal]

    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
