from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.venue_source import (
    VenueSourceCreate,
    VenueSourceUpdate,
    VenueSourceResponse,
)
from app.services.venue_source import VenueSourceService

router = APIRouter(prefix="/venue-sources", tags=["Venue Sources"])


@router.post("/", response_model=VenueSourceResponse, status_code=status.HTTP_201_CREATED)
def create_venue_source(data: VenueSourceCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VenueSourceService.create(db, data, current_user.id)


@router.get("/", response_model=list[VenueSourceResponse])
def get_venue_sources(venue_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VenueSourceService.get_all_by_venue(db, venue_id, current_user.id)


@router.get("/{source_id}", response_model=VenueSourceResponse)
def get_venue_source(source_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VenueSourceService.get_by_id(db, source_id, current_user.id)


@router.patch("/{source_id}", response_model=VenueSourceResponse)
def update_venue_source(
    source_id: UUID,
    data: VenueSourceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return VenueSourceService.update(db, source_id, data, current_user.id)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_venue_source(source_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    VenueSourceService.delete(db, source_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
