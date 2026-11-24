from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.event import Event


class EventRepository:

    @staticmethod
    def create(db: Session, data: dict) -> Event:
        event = Event(**data)
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def get_by_id(db: Session, event_id: UUID):
        statement = select(Event).where(Event.id == event_id)
        return db.scalars(statement).first()

    @staticmethod
    def get_all(db: Session, organization_id: UUID, *, project_id: Optional[UUID] = None,
                offset: int = 0, limit: int = 50):
        statement = (
            select(Event)
            .where(Event.organization_id == organization_id)
            .order_by(Event.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if project_id is not None:
            statement = statement.where(Event.project_id == project_id)
        return db.scalars(statement).all()

    @staticmethod
    def get_by_project(db: Session, organization_id: UUID, project_id: UUID):
        statement = (
            select(Event)
            .where(
                Event.organization_id == organization_id,
                Event.project_id == project_id,
            )
            .order_by(Event.event_date, Event.created_at, Event.id)
        )
        return db.scalars(statement).all()

    @staticmethod
    def get_by_venue(db: Session, organization_id: UUID, venue_id: UUID):
        statement = (
            select(Event)
            .where(
                Event.organization_id == organization_id,
                Event.venue_id == venue_id,
            )
            .order_by(Event.event_date, Event.created_at, Event.id)
        )
        return db.scalars(statement).all()

    @staticmethod
    def update(db: Session, event: Event, data: dict):
        expected_version = data.pop("version", None)
        if expected_version is not None and event.version != expected_version:
            raise HTTPException(status_code=409, detail="Event was updated by another request")
        result = db.execute(
            update(Event)
            .where(Event.id == event.id, Event.version == event.version)
            .values(**data, version=Event.version + 1)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="Event was updated by another request")
        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def delete(db: Session, event: Event):
        db.delete(event)
        db.commit()
