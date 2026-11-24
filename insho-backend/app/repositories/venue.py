from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.venue import Venue


class VenueRepository:

    @staticmethod
    def create(db: Session, data: dict) -> Venue:
        venue = Venue(**data)
        db.add(venue)
        db.commit()
        db.refresh(venue)
        return venue

    @staticmethod
    def get_by_id(db: Session, venue_id: UUID):
        statement = select(Venue).where(Venue.id == venue_id)
        return db.scalars(statement).first()

    @staticmethod
    def get_all(db: Session, organization_id: UUID):
        statement = (
            select(Venue)
            .where(Venue.organization_id == organization_id)
            .order_by(Venue.created_at.desc())
        )
        return db.scalars(statement).all()

    @staticmethod
    def update(db: Session, venue: Venue, data: dict):
        for field, value in data.items():
            setattr(venue, field, value)

        db.commit()
        db.refresh(venue)
        return venue

    @staticmethod
    def delete(db: Session, venue: Venue):
        db.delete(venue)
        db.commit()
