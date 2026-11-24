
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_type import EventType


class EventTypeRepository:

    @staticmethod
    def create(db: Session, data: dict) -> EventType:
        event_type = EventType(**data)

        db.add(event_type)
        db.commit()
        db.refresh(event_type)

        return event_type

    @staticmethod
    def get_by_id(db: Session, event_type_id: UUID):
        statement = select(EventType).where(
            EventType.id == event_type_id
        )
        return db.scalars(statement).first()

    @staticmethod
    def get_all(db: Session):
        statement = select(EventType).order_by(EventType.name)
        return db.scalars(statement).all()

    @staticmethod
    def update(db: Session, event_type: EventType, data: dict):
        for field, value in data.items():
            setattr(event_type, field, value)

        db.commit()
        db.refresh(event_type)

        return event_type

    @staticmethod
    def delete(db: Session, event_type: EventType):
        db.delete(event_type)
        db.commit()
