from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.venue import VenueRepository
from app.schemas.venue import VenueCreate, VenueUpdate
from app.security import require_membership, require_venue_access


class VenueService:

    @staticmethod
    def create(db: Session, data: VenueCreate, user_id: UUID):
        require_membership(db, user_id, data.organization_id, write=True)

        return VenueRepository.create(db, data.model_dump())

    @staticmethod
    def get_by_id(db: Session, venue_id: UUID, user_id: UUID):
        require_venue_access(db, user_id, venue_id)
        venue = VenueRepository.get_by_id(db, venue_id)

        if not venue:
            raise HTTPException(status_code=404, detail="Venue not found")

        return venue

    @staticmethod
    def get_all(db: Session, organization_id: UUID, user_id: UUID):
        require_membership(db, user_id, organization_id)

        return VenueRepository.get_all(db, organization_id)

    @staticmethod
    def update(db: Session, venue_id: UUID, data: VenueUpdate, user_id: UUID):
        venue = require_venue_access(db, user_id, venue_id, write=True)
        payload = data.model_dump(exclude_unset=True)

        if "name" in payload and payload["name"] is None:
            raise HTTPException(status_code=422, detail="Venue name cannot be null")

        return VenueRepository.update(db, venue, payload)

    @staticmethod
    def delete(db: Session, venue_id: UUID, user_id: UUID):
        venue = require_venue_access(db, user_id, venue_id, write=True)
        VenueRepository.delete(db, venue)
