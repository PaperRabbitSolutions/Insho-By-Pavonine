
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repositories.event_type import EventTypeRepository
from app.schemas.event_type import EventTypeCreate, EventTypeUpdate
from app.security import require_catalog_admin


class EventTypeService:

    @staticmethod
    def create(db: Session, data: EventTypeCreate, user_id: UUID):
        require_catalog_admin(db, user_id)
        payload = data.model_dump()
        return EventTypeRepository.create(db, payload)

    @staticmethod
    def get_by_id(db: Session, event_type_id: UUID):
        event_type = EventTypeRepository.get_by_id(db, event_type_id)

        if not event_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event type not found"
            )

        return event_type

    @staticmethod
    def get_all(db: Session):
        return EventTypeRepository.get_all(db)

    @staticmethod
    def update(
        db: Session,
        event_type_id: UUID,
        data: EventTypeUpdate,
        user_id: UUID,
    ):
        require_catalog_admin(db, user_id)
        event_type = EventTypeService.get_by_id(db, event_type_id)
        payload = data.model_dump(exclude_unset=True)

        if "name" in payload and payload["name"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Event type name cannot be null"
            )

        return EventTypeRepository.update(db, event_type, payload)

    @staticmethod
    def delete(db: Session, event_type_id: UUID, user_id: UUID):
        require_catalog_admin(db, user_id)
        event_type = EventTypeService.get_by_id(db, event_type_id)
        EventTypeRepository.delete(db, event_type)
