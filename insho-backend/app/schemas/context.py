from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.design_dna import DesignDNAResponse
from app.schemas.event import EventResponse
from app.schemas.project import ProjectResponse
from app.schemas.project_memory import (
    ContextMemoryItem,
    ProjectMemoryResponse,
)
from app.schemas.venue import VenueResponse
from app.schemas.venue_zone import VenueZoneResponse


class EventContextItem(BaseModel):
    event: EventResponse
    venue: Optional[VenueResponse] = None
    venue_zones: List[VenueZoneResponse] = Field(default_factory=list)


class ProjectContextResponse(BaseModel):
    project: ProjectResponse
    events: List[EventContextItem] = Field(default_factory=list)
    design_dna: Optional[DesignDNAResponse] = None
    project_memory: Optional[ProjectMemoryResponse] = None
    memory_items: List[ContextMemoryItem] = Field(default_factory=list)


class EventContextResponse(BaseModel):
    event: EventResponse
    project: Optional[ProjectResponse] = None
    design_dna: Optional[DesignDNAResponse] = None
    project_memory: Optional[ProjectMemoryResponse] = None
    memory_items: List[ContextMemoryItem] = Field(default_factory=list)
    venue: Optional[VenueResponse] = None
    venue_zones: List[VenueZoneResponse] = []


class VenueContextResponse(BaseModel):
    venue: VenueResponse
    venue_zones: List[VenueZoneResponse]
    events: List[EventResponse]
    projects: List[ProjectResponse]
    design_dna: List[DesignDNAResponse]
    project_memories: List[ProjectMemoryResponse]
    memory_items: List[ContextMemoryItem]
