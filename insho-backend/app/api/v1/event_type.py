
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.event_type import (
    EventTypeCreate,
    EventTypeUpdate,
    EventTypeResponse,
)
from app.services.event_type import EventTypeService


router = APIRouter(
    prefix="/event-types",
    tags=["Event Types"]
)


@router.post(
    "/",
    response_model=EventTypeResponse,
    status_code=status.HTTP_201_CREATED
)
def create_event_type(
    data: EventTypeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return EventTypeService.create(db, data, current_user.id)


@router.get(
    "/",
    response_model=list[EventTypeResponse]
)
def get_event_types(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return EventTypeService.get_all(db)


@router.get(
    "/{event_type_id}",
    response_model=EventTypeResponse
)
def get_event_type(
    event_type_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return EventTypeService.get_by_id(db, event_type_id)


@router.patch(
    "/{event_type_id}",
    response_model=EventTypeResponse
)
def update_event_type(
    event_type_id: UUID,
    data: EventTypeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return EventTypeService.update(db, event_type_id, data, current_user.id)


@router.delete(
    "/{event_type_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_event_type(
    event_type_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    EventTypeService.delete(db, event_type_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
