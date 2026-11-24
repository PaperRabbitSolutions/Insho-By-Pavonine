from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.venue_source import VenueSource


class VenueSourceRepository:

    @staticmethod
    def create(db: Session, data: dict) -> VenueSource:
        source = VenueSource(**data)
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    @staticmethod
    def get_by_id(db: Session, source_id: UUID):
        statement = select(VenueSource).where(VenueSource.id == source_id)
        return db.scalars(statement).first()

    @staticmethod
    def get_all_by_venue(db: Session, venue_id: UUID):
        statement = (
            select(VenueSource)
            .where(VenueSource.venue_id == venue_id)
            .order_by(VenueSource.created_at.desc())
        )
        return db.scalars(statement).all()

    @staticmethod
    def update(db: Session, source: VenueSource, data: dict):
        for field, value in data.items():
            setattr(source, field, value)

        db.commit()
        db.refresh(source)
        return source

    @staticmethod
    def delete(db: Session, source: VenueSource):
        db.delete(source)
        db.commit()
