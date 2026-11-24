from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.auth import get_current_user
from app.models.user import User
from app.schemas.project_memory import (
    ProjectMemoryCreate,
    ProjectMemoryItemCreate,
    ProjectMemoryItemResponse,
    ProjectMemoryItemUpdate,
    ProjectMemoryUpdate,
    ProjectMemoryResponse,
    MemoryArchive, MemoryCategory, MemoryRevisionRead,
)
from app.services.project_memory import ProjectMemoryService
from app.services.project_memory_item import ProjectMemoryItemService

router = APIRouter(prefix="/project-memory", tags=["Project Memory"])
RequestID = Annotated[Optional[UUID], Header(alias="X-Request-ID")]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post(
    "/items",
    response_model=ProjectMemoryItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_memory_item(
    data: ProjectMemoryItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    request_id: RequestID = None,
):
    return ProjectMemoryItemService.create(db, data, current_user.id, request_id=request_id)


@router.get(
    "/project/{project_id}/items",
    response_model=list[ProjectMemoryItemResponse],
)
def list_project_memory_items(
    project_id: UUID,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    category: Optional[MemoryCategory] = None,
    event_id: Optional[UUID] = None,
    offset: Offset = 0,
    limit: Annotated[Optional[int], Query(ge=1, le=100)] = None,
):
    return ProjectMemoryItemService.get_by_project(
        db, project_id, current_user.id, include_archived=include_archived,
        category=category, event_id=event_id, offset=offset, limit=limit,
    )


@router.get("/items/{item_id}", response_model=ProjectMemoryItemResponse)
def get_project_memory_item(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ProjectMemoryItemService.get_by_id(db, item_id, current_user.id)


@router.patch("/items/{item_id}", response_model=ProjectMemoryItemResponse)
def update_project_memory_item(
    item_id: UUID,
    data: ProjectMemoryItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    request_id: RequestID = None,
):
    return ProjectMemoryItemService.update(db, item_id, data, current_user.id, request_id=request_id)


@router.get("/items/{item_id}/history", response_model=list[MemoryRevisionRead])
def memory_history(item_id: UUID, offset: Offset = 0, limit: Limit = 50,
                   current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ProjectMemoryItemService.history(db, item_id, current_user.id, offset=offset, limit=limit)


@router.post("/items/{item_id}/archive", response_model=ProjectMemoryItemResponse)
def archive_memory_item(item_id: UUID, data: MemoryArchive, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db), request_id: RequestID = None):
    patch = ProjectMemoryItemUpdate(status="archived", expected_version=data.expected_version, reason=data.reason)
    return ProjectMemoryItemService.update(db, item_id, patch, current_user.id, request_id=request_id)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_memory_item(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    request_id: RequestID = None,
):
    patch = ProjectMemoryItemUpdate(status="archived", reason="Archived through the existing delete endpoint.")
    ProjectMemoryItemService.update(db, item_id, patch, current_user.id, request_id=request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/",
    response_model=ProjectMemoryResponse,
    status_code=status.HTTP_201_CREATED
)
def create_project_memory(
    data: ProjectMemoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return ProjectMemoryService.create(db, data, current_user.id)


@router.get(
    "/project/{project_id}",
    response_model=ProjectMemoryResponse
)
def get_project_memory_by_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return ProjectMemoryService.get_by_project(db, project_id, current_user.id)


@router.get("/{memory_id}", response_model=ProjectMemoryResponse)
def get_project_memory(
    memory_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return ProjectMemoryService.get_by_id(db, memory_id, current_user.id)


@router.patch(
    "/{memory_id}",
    response_model=ProjectMemoryResponse
)
def update_project_memory(
    memory_id: UUID,
    data: ProjectMemoryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return ProjectMemoryService.update(db, memory_id, data, current_user.id)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_project_memory(
    memory_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ProjectMemoryService.delete(db, memory_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
