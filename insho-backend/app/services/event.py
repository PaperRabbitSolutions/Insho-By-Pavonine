from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.event_type import EventType
from app.models.venue import Venue
from app.repositories.event import EventRepository
from app.schemas.event import EventCreate, EventUpdate
from app.models.spatial import SpatialScene
from app.services import procurement_scope
from app.services.project_memory_item import ProjectMemoryItemService
from app.services.spatial_policy import delete_if_scenes_unlocked
from app.security import require_event_access, require_membership, require_project_access


class EventService:

    @staticmethod
    def create(db: Session, data: EventCreate, user_id: UUID):
        project = require_project_access(db, user_id, data.project_id, write=True)

        if data.event_type_id and not db.get(EventType, data.event_type_id):
            raise HTTPException(status_code=404, detail="Event type not found")

        if data.venue_id:
            venue = db.get(Venue, data.venue_id)
            if not venue or venue.organization_id != project.organization_id:
                raise HTTPException(status_code=404, detail="Resource not found")

        payload = data.model_dump(exclude={"project_id"})
        payload["project_id"] = project.id
        payload["organization_id"] = project.organization_id
        if payload.get("currency"):
            payload["currency"] = payload["currency"].upper()
        return EventRepository.create(db, payload)

    @staticmethod
    def get_by_id(db: Session, event_id: UUID, user_id: UUID):
        require_event_access(db, user_id, event_id)
        event = EventRepository.get_by_id(db, event_id)

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        return event

    @staticmethod
    def get_all(db: Session, organization_id: UUID, user_id: UUID, *, project_id: Optional[UUID] = None,
                offset: int = 0, limit: int = 50):
        require_membership(db, user_id, organization_id)
        if project_id is not None:
            project = require_project_access(db, user_id, project_id)
            if project.organization_id != organization_id:
                raise HTTPException(status_code=404, detail="Resource not found")
        return EventRepository.get_all(
            db, organization_id, project_id=project_id, offset=offset, limit=limit
        )

    @staticmethod
    def get_for_project(db: Session, project_id: UUID, user_id: UUID, *, offset: int = 0,
                        limit: int = 50):
        project = require_project_access(db, user_id, project_id)
        return EventRepository.get_all(
            db, project.organization_id, project_id=project_id, offset=offset, limit=limit
        )

    @staticmethod
    def update(db: Session, event_id: UUID, data: EventUpdate, user_id: UUID):
        event = require_event_access(db, user_id, event_id, write=True)
        payload = data.model_dump(exclude_unset=True)
        if payload.get("currency"):
            payload["currency"] = payload["currency"].upper()

        if "name" in payload and payload["name"] is None:
            raise HTTPException(status_code=422, detail="Event name cannot be null")
        if "status" in payload and payload["status"] is None:
            raise HTTPException(status_code=422, detail="Event status cannot be null")

        if payload.get("event_type_id") is not None:
            if not db.get(EventType, payload["event_type_id"]):
                raise HTTPException(status_code=404, detail="Event type not found")

        if payload.get("venue_id") is not None:
            venue = db.get(Venue, payload["venue_id"])
            if not venue or venue.organization_id != event.organization_id:
                raise HTTPException(status_code=404, detail="Resource not found")

        start = payload.get("starts_at", event.starts_at)
        end = payload.get("ends_at", event.ends_at)
        event_date = payload.get("event_date", event.event_date)
        if start is not None and end is not None and end <= start:
            raise HTTPException(status_code=422, detail="ends_at must be later than starts_at")
        if event_date is not None and start is not None and event_date != start.date():
            raise HTTPException(status_code=422, detail="event_date must match the date of starts_at")

        allowed_transitions = {
            "draft": {"scheduled", "cancelled"},
            "scheduled": {"completed", "cancelled"},
            "completed": {"completed"},
            "cancelled": {"cancelled"},
        }
        new_status = payload.get("status", event.status)
        if new_status != event.status and new_status not in allowed_transitions[event.status]:
            raise HTTPException(status_code=409, detail="Unsupported event status transition")

        return EventRepository.update(db, event, payload)

    @staticmethod
    def delete(db: Session, event_id: UUID, user_id: UUID):
        event = require_event_access(db, user_id, event_id, write=True)
        project = (procurement_scope.project_scope(db, event.project_id, user_id, write=True)
                   if event.project_id is not None else None)

        def delete():
            if project is not None:
                procurement_scope.protect_container_history(db, project, event_id=event.id)
                ProjectMemoryItemService.archive_for_event(db, project.id, event.id, user_id)
            EventRepository.delete(db, event)

        delete_if_scenes_unlocked(db, SpatialScene.event_id == event.id, delete)
