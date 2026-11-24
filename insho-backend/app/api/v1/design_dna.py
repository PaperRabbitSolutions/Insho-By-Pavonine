from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.design_dna import (
    DesignDNACreate,
    DesignDNAUpdate,
    DesignDNAResponse,
)
from app.services.design_dna import DesignDNAService

router = APIRouter(prefix="/design-dna", tags=["Design DNA"])


@router.post(
    "/",
    response_model=DesignDNAResponse,
    status_code=status.HTTP_201_CREATED
)
def create_design_dna(
    data: DesignDNACreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return DesignDNAService.create(db, data, current_user.id)


@router.get(
    "/project/{project_id}",
    response_model=DesignDNAResponse
)
def get_design_dna_by_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return DesignDNAService.get_by_project(db, project_id, current_user.id)


@router.get("/{design_dna_id}", response_model=DesignDNAResponse)
def get_design_dna(
    design_dna_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return DesignDNAService.get_by_id(db, design_dna_id, current_user.id)


@router.patch(
    "/{design_dna_id}",
    response_model=DesignDNAResponse
)
def update_design_dna(
    design_dna_id: UUID,
    data: DesignDNAUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return DesignDNAService.update(db, design_dna_id, data, current_user.id)


@router.delete(
    "/{design_dna_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_design_dna(
    design_dna_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    DesignDNAService.delete(db, design_dna_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
