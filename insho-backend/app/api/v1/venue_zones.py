from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.venue_zone import (
    VenueZoneCreate,
    VenueZoneUpdate,
    VenueZoneResponse,
)
from app.services.venue_zone import VenueZoneService

router = APIRouter(prefix="/venue-zones", tags=["Venue Zones"])


@router.post("/", response_model=VenueZoneResponse, status_code=status.HTTP_201_CREATED)
def create_venue_zone(data: VenueZoneCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VenueZoneService.create(db, data, current_user.id)


@router.get("/", response_model=list[VenueZoneResponse])
def get_venue_zones(venue_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VenueZoneService.get_all_by_venue(db, venue_id, current_user.id)


@router.get("/{zone_id}", response_model=VenueZoneResponse)
def get_venue_zone(zone_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VenueZoneService.get_by_id(db, zone_id, current_user.id)


@router.patch("/{zone_id}", response_model=VenueZoneResponse)
def update_venue_zone(
    zone_id: UUID,
    data: VenueZoneUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return VenueZoneService.update(db, zone_id, data, current_user.id)


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_venue_zone(zone_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    VenueZoneService.delete(db, zone_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
