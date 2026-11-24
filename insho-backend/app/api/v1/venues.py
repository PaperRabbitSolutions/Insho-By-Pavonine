from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.venue import VenueCreate, VenueUpdate, VenueResponse
from app.services.venue import VenueService

router = APIRouter(prefix="/venues", tags=["Venues"])


@router.post("/", response_model=VenueResponse, status_code=status.HTTP_201_CREATED)
def create_venue(
    data: VenueCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return VenueService.create(db, data, current_user.id)


@router.get("/", response_model=list[VenueResponse])
def get_venues(
    organization_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return VenueService.get_all(db, organization_id, current_user.id)


@router.get("/{venue_id}", response_model=VenueResponse)
def get_venue(
    venue_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return VenueService.get_by_id(db, venue_id, current_user.id)


@router.patch("/{venue_id}", response_model=VenueResponse)
def update_venue(
    venue_id: UUID,
    data: VenueUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return VenueService.update(db, venue_id, data, current_user.id)


@router.delete("/{venue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_venue(
    venue_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    VenueService.delete(db, venue_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
