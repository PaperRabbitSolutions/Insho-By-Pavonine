from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GenerateDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: Optional[UUID] = None
    additional_brief: Optional[str] = Field(default=None, max_length=6000)
    reference_asset_ids: list[UUID] = Field(default_factory=list, max_length=30)


class MoodColor(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    hex: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    role: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=500)


class MoodSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    atmosphere: str = Field(min_length=1, max_length=1000)
    emotional_direction: list[str] = Field(min_length=1, max_length=12)
    keywords: list[str] = Field(min_length=1, max_length=20)
    color_story: str = Field(min_length=1, max_length=1200)
    colors: list[MoodColor] = Field(min_length=2, max_length=8)
    floral_direction: list[str] = Field(default_factory=list, max_length=12)
    materials: list[str] = Field(default_factory=list, max_length=15)
    textures: list[str] = Field(default_factory=list, max_length=12)
    lighting_atmosphere: str = Field(min_length=1, max_length=800)
    decor_and_styling: list[str] = Field(default_factory=list, max_length=15)
    visual_inspiration: list[str] = Field(default_factory=list, max_length=10)
    rationale: str = Field(min_length=1, max_length=1600)
    uncertainties: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0, le=1)


class StyleSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    creative_direction: str = Field(min_length=1, max_length=1200)
    design_language: list[str] = Field(min_length=1, max_length=16)
    visual_identity: str = Field(min_length=1, max_length=1000)
    architectural_language: list[str] = Field(default_factory=list, max_length=12)
    forms_and_shapes: list[str] = Field(default_factory=list, max_length=12)
    motifs: list[str] = Field(default_factory=list, max_length=12)
    composition_principles: list[str] = Field(default_factory=list, max_length=12)
    spatial_character: str = Field(min_length=1, max_length=800)
    guest_experience: str = Field(min_length=1, max_length=1000)
    formality: Optional[str] = Field(default=None, max_length=100)
    luxury_level: Optional[str] = Field(default=None, max_length=100)
    visual_hierarchy: list[str] = Field(default_factory=list, max_length=12)
    styling_principles: list[str] = Field(default_factory=list, max_length=15)
    materials_and_finishes: list[str] = Field(default_factory=list, max_length=15)
    lighting_character: str = Field(min_length=1, max_length=800)
    photography_direction: str = Field(min_length=1, max_length=800)
    rationale: str = Field(min_length=1, max_length=1600)
    conflicts: list[str] = Field(default_factory=list, max_length=10)


class PlanZone(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    zone_type: str = Field(min_length=1, max_length=60)
    purpose: str = Field(min_length=1, max_length=500)
    location_description: str = Field(min_length=1, max_length=500)
    dimensions: Optional[dict[str, Any]] = None
    dimensions_source: Literal["user_provided", "project_data", "unknown"] = "unknown"
    capacity: Optional[int] = Field(default=None, ge=0)
    capacity_source: Literal["user_provided", "project_data", "suggestion", "unknown"] = "unknown"
    rationale: str = Field(min_length=1, max_length=700)
    safety_considerations: list[str] = Field(default_factory=list, max_length=10)


class PlanSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1200)
    zones: list[PlanZone] = Field(min_length=1, max_length=30)
    adjacencies: list[dict[str, str]] = Field(default_factory=list, max_length=40)
    guest_flow: list[str] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    unknown_constraints: list[str] = Field(default_factory=list, max_length=20)
    conflicts: list[str] = Field(default_factory=list, max_length=12)
    rationale: str = Field(min_length=1, max_length=1600)
    construction_accuracy: Literal["conceptual_only"] = "conceptual_only"


class ConceptView(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    view: Literal["front", "side", "birds_eye", "three_quarter", "day", "night", "alternative"]
    design_intent: str = Field(min_length=1, max_length=800)
    focal_points: list[str] = Field(default_factory=list, max_length=10)
    consistency_notes: list[str] = Field(default_factory=list, max_length=10)
    image_generation_status: Literal["not_requested", "unavailable"] = "not_requested"


class VisionSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept_summary: str = Field(min_length=1, max_length=1200)
    views: list[ConceptView] = Field(min_length=1, max_length=12)
    based_on_modules: list[Literal["mood", "style", "plan"]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class DesignElementSuggestion(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: Literal["floral", "decor", "furniture", "lighting", "scenic", "architectural", "stage", "fabric", "flooring", "structure", "prop", "other"]
    description: str = Field(min_length=1, max_length=1000)
    design_role: Optional[str] = Field(default=None, max_length=300)
    related_zone: Optional[str] = Field(default=None, max_length=100)
    style: Optional[str] = Field(default=None, max_length=300)
    materials: list[str] = Field(default_factory=list, max_length=12)
    color: Optional[str] = Field(default=None, max_length=100)
    dimensions: Optional[dict[str, Any]] = None
    quantity: Optional[float] = Field(default=None, gt=0)
    quantity_source: Literal["user_provided", "project_data", "unknown"] = "unknown"
    rationale: Optional[str] = Field(default=None, max_length=800)
    source_reference: Optional[str] = Field(default=None, max_length=300)
    confidence: float = Field(ge=0, le=1)
    notes: list[str] = Field(default_factory=list, max_length=10)
    relationships: list[str] = Field(default_factory=list, max_length=12)


class ElementsSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    elements: list[DesignElementSuggestion] = Field(min_length=1, max_length=100)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)


class ArtifactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: dict[str, Any]


class ArtifactResponse(BaseModel):
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    module: str
    version: int
    status: str
    source: str
    content: dict[str, Any]
    based_on: dict[str, Any]
    context_stale: bool = False
    ai_execution_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class ElementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Optional[bool] = None
    details: Optional[dict[str, Any]] = None


class ElementResponse(BaseModel):
    id: UUID
    artifact_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    name: str
    category: str
    details: dict[str, Any]
    source: str
    confirmed_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
