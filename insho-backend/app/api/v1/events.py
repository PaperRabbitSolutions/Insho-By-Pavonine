from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.event import EventCreate, EventUpdate, EventResponse
from app.services.event import EventService

router = APIRouter(prefix="/events", tags=["Events"])


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(
    data: EventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return EventService.create(db, data, current_user.id)


@router.get("/", response_model=list[EventResponse])
def get_events(
    organization_id: UUID,
    project_id: Optional[UUID] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return EventService.get_all(
        db, organization_id, current_user.id, project_id=project_id,
        offset=offset, limit=limit,
    )


@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return EventService.get_by_id(db, event_id, current_user.id)


@router.patch("/{event_id}", response_model=EventResponse)
def update_event(
    event_id: UUID,
    data: EventUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return EventService.update(db, event_id, data, current_user.id)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    EventService.delete(db, event_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
