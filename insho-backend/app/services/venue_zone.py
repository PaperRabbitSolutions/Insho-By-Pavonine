from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.venue_zone import VenueZoneRepository
from app.schemas.venue_zone import VenueZoneCreate, VenueZoneUpdate
from app.security import require_venue_access


class VenueZoneService:

    @staticmethod
    def create(db: Session, data: VenueZoneCreate, user_id: UUID):
        require_venue_access(db, user_id, data.venue_id, write=True)

        return VenueZoneRepository.create(db, data.model_dump())

    @staticmethod
    def get_by_id(db: Session, zone_id: UUID, user_id: UUID):
        zone = VenueZoneRepository.get_by_id(db, zone_id)

        if not zone:
            raise HTTPException(status_code=404, detail="Venue zone not found")
        require_venue_access(db, user_id, zone.venue_id)

        return zone

    @staticmethod
    def get_all_by_venue(db: Session, venue_id: UUID, user_id: UUID):
        require_venue_access(db, user_id, venue_id)

        return VenueZoneRepository.get_all_by_venue(db, venue_id)

    @staticmethod
    def update(db: Session, zone_id: UUID, data: VenueZoneUpdate, user_id: UUID):
        zone = VenueZoneService.get_by_id(db, zone_id, user_id)
        require_venue_access(db, user_id, zone.venue_id, write=True)
        payload = data.model_dump(exclude_unset=True)

        if "name" in payload and payload["name"] is None:
            raise HTTPException(
                status_code=422,
                detail="Venue zone name cannot be null"
            )

        return VenueZoneRepository.update(db, zone, payload)

    @staticmethod
    def delete(db: Session, zone_id: UUID, user_id: UUID):
        zone = VenueZoneService.get_by_id(db, zone_id, user_id)
        require_venue_access(db, user_id, zone.venue_id, write=True)
        VenueZoneRepository.delete(db, zone)
