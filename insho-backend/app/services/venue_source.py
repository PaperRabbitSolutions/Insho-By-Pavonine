from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.venue_source import VenueSourceRepository
from app.schemas.venue_source import VenueSourceCreate, VenueSourceUpdate
from app.security import require_venue_access


class VenueSourceService:

    @staticmethod
    def create(db: Session, data: VenueSourceCreate, user_id: UUID):
        require_venue_access(db, user_id, data.venue_id, write=True)

        payload = data.model_dump()
        return VenueSourceRepository.create(db, payload)

    @staticmethod
    def get_by_id(db: Session, source_id: UUID, user_id: UUID):
        source = VenueSourceRepository.get_by_id(db, source_id)

        if not source:
            raise HTTPException(status_code=404, detail="Venue source not found")
        require_venue_access(db, user_id, source.venue_id)

        return source

    @staticmethod
    def get_all_by_venue(db: Session, venue_id: UUID, user_id: UUID):
        require_venue_access(db, user_id, venue_id)

        return VenueSourceRepository.get_all_by_venue(db, venue_id)

    @staticmethod
    def update(db: Session, source_id: UUID, data: VenueSourceUpdate, user_id: UUID):
        source = VenueSourceService.get_by_id(db, source_id, user_id)
        require_venue_access(db, user_id, source.venue_id, write=True)
        payload = data.model_dump(exclude_unset=True)

        if "source_type" in payload and payload["source_type"] is None:
            raise HTTPException(
                status_code=422,
                detail="Source type cannot be null"
            )

        return VenueSourceRepository.update(db, source, payload)

    @staticmethod
    def delete(db: Session, source_id: UUID, user_id: UUID):
        source = VenueSourceService.get_by_id(db, source_id, user_id)
        require_venue_access(db, user_id, source.venue_id, write=True)
        VenueSourceRepository.delete(db, source)
