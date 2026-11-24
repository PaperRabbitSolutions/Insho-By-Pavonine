from typing import Dict
from uuid import UUID

from sqlalchemy.orm import Session

from app.repositories.design_dna import DesignDNARepository
from app.repositories.event import EventRepository
from app.repositories.project import ProjectRepository
from app.repositories.project_memory import ProjectMemoryRepository
from app.repositories.project_memory_item import ProjectMemoryItemRepository
from app.repositories.venue import VenueRepository
from app.repositories.venue_zone import VenueZoneRepository
from app.security import require_event_access, require_project_access, require_venue_access


class ContextService:

    @staticmethod
    def _event_context_item(db: Session, event):
        venue = None
        zones = []
        if event.venue_id is not None:
            candidate = VenueRepository.get_by_id(db, event.venue_id)
            if candidate is not None and candidate.organization_id == event.organization_id:
                venue = candidate
                zones = VenueZoneRepository.get_all_by_venue(db, venue.id)
        return {"event": event, "venue": venue, "venue_zones": zones}

    @staticmethod
    def get_project_context(db: Session, project_id: UUID, user_id: UUID):
        project = require_project_access(db, user_id, project_id)
        events = EventRepository.get_by_project(
            db, project.organization_id, project.id
        )
        return {
            "project": project,
            "events": [ContextService._event_context_item(db, event) for event in events],
            "design_dna": DesignDNARepository.get_by_project(db, project.id),
            "project_memory": ProjectMemoryRepository.get_by_project(db, project.id),
            "memory_items": ProjectMemoryItemRepository.get_by_project(db, project.id),
        }

    @staticmethod
    def get_event_context(db: Session, event_id: UUID, user_id: UUID):
        event = require_event_access(db, user_id, event_id)
        project = None
        design_dna = None
        project_memory = None
        memory_items = []
        if event.project_id is not None:
            project = require_project_access(db, user_id, event.project_id)
            if project.organization_id == event.organization_id:
                design_dna = DesignDNARepository.get_by_project(db, project.id)
                project_memory = ProjectMemoryRepository.get_by_project(db, project.id)
                memory_items = ProjectMemoryItemRepository.get_context_for_event(
                    db, project.id, event.id
                )
            else:
                project = None

        related = ContextService._event_context_item(db, event)
        return {
            "event": event,
            "project": project,
            "design_dna": design_dna,
            "project_memory": project_memory,
            "memory_items": memory_items,
            "venue": related["venue"],
            "venue_zones": related["venue_zones"],
        }

    @staticmethod
    def get_venue_context(db: Session, venue_id: UUID, user_id: UUID):
        venue = require_venue_access(db, user_id, venue_id)
        zones = VenueZoneRepository.get_all_by_venue(db, venue.id)
        events = EventRepository.get_by_venue(db, venue.organization_id, venue.id)

        projects: Dict[UUID, object] = {}
        design_dna: Dict[UUID, object] = {}
        project_memories: Dict[UUID, object] = {}
        memory_items: Dict[UUID, object] = {}
        related_events = []

        for event in events:
            if event.project_id is None:
                related_events.append(event)
                continue
            project = require_project_access(db, user_id, event.project_id)
            if project.organization_id != venue.organization_id:
                continue
            related_events.append(event)
            projects[project.id] = project

            dna = DesignDNARepository.get_by_project(db, project.id)
            if dna is not None:
                design_dna[dna.id] = dna
            memory = ProjectMemoryRepository.get_by_project(db, project.id)
            if memory is not None:
                project_memories[memory.id] = memory
            for item in ProjectMemoryItemRepository.get_context_for_event(
                db, project.id, event.id
            ):
                memory_items[item.id] = item

        return {
            "venue": venue,
            "venue_zones": zones,
            "events": related_events,
            "projects": sorted(projects.values(), key=lambda row: (row.name, str(row.id))),
            "design_dna": list(design_dna.values()),
            "project_memories": list(project_memories.values()),
            "memory_items": list(memory_items.values()),
        }
