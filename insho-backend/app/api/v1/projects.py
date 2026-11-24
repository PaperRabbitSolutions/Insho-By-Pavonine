from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse
from app.services.project import ProjectService
from app.services.event import EventService
from app.schemas.event import EventResponse

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ProjectService.create(db, data, current_user.id)


@router.get("/", response_model=list[ProjectResponse])
def get_projects(
    organization_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    project_status: Optional[str] = Query(default=None, alias="status", pattern="^(draft|active|completed|archived)$"),
    workspace_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ProjectService.get_all(
        db, organization_id, current_user.id, offset=offset, limit=limit,
        status=project_status, workspace_id=workspace_id,
    )


@router.get("/{project_id}/events", response_model=list[EventResponse])
def get_project_events(
    project_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return EventService.get_for_project(db, project_id, current_user.id, offset=offset, limit=limit)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ProjectService.get_by_id(db, project_id, current_user.id)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    data: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ProjectService.update(db, project_id, data, current_user.id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ProjectService.delete(db, project_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
