from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.venue_zone import VenueZone


class VenueZoneRepository:

    @staticmethod
    def create(db: Session, data: dict) -> VenueZone:
        zone = VenueZone(**data)
        db.add(zone)
        db.commit()
        db.refresh(zone)
        return zone

    @staticmethod
    def get_by_id(db: Session, zone_id: UUID):
        statement = select(VenueZone).where(VenueZone.id == zone_id)
        return db.scalars(statement).first()

    @staticmethod
    def get_all_by_venue(db: Session, venue_id: UUID):
        statement = (
            select(VenueZone)
            .where(VenueZone.venue_id == venue_id)
            .order_by(VenueZone.created_at.desc(), VenueZone.id)
        )
        return db.scalars(statement).all()

    @staticmethod
    def update(db: Session, zone: VenueZone, data: dict):
        for field, value in data.items():
            setattr(zone, field, value)

        db.commit()
        db.refresh(zone)
        return zone

    @staticmethod
    def delete(db: Session, zone: VenueZone):
        db.delete(zone)
        db.commit()
