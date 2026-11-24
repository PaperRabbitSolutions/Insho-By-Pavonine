from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.design_version import (
    ActivityRead, ArchiveBranch, BranchCreate, BranchRead, PresentationPin, PromoteBranch, RestoreVersion,
    VersionComparison, VersionDetail, VersionRead, VersionWrite,
)
from app.services import design_version as versions


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/projects/{project_id}", tags=["Design Versions"], dependencies=[Depends(private_response)])
CurrentUser = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
RequestID = Annotated[Optional[UUID], Header(alias="X-Request-ID")]


@router.post("/design-versions", response_model=VersionDetail, status_code=201)
def create_version(project_id: UUID, data: VersionWrite, user: CurrentUser, db: Database,
                   event_id: Optional[UUID] = None, request_id: RequestID = None):
    return versions.write(db, project_id, event_id, user.id, data, request_id=request_id)


@router.get("/design-versions", response_model=list[VersionRead])
def list_versions(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                  offset: Offset = 0, limit: Limit = 50):
    return versions.history(db, project_id, event_id, user.id, offset=offset, limit=limit)


@router.get("/design-versions/current", response_model=VersionDetail)
def current_version(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None):
    return versions.current(db, project_id, event_id, user.id)


@router.get("/design-versions/compare", response_model=VersionComparison)
def compare_versions(project_id: UUID, from_version_id: UUID, to_version_id: UUID, user: CurrentUser, db: Database):
    return versions.compare(db, project_id, from_version_id, to_version_id, user.id)


@router.post("/design-versions/restore", response_model=VersionDetail, status_code=201)
def restore_version(project_id: UUID, data: RestoreVersion, user: CurrentUser, db: Database, request_id: RequestID = None):
    return versions.restore(db, project_id, user.id, data, request_id=request_id)


@router.get("/design-versions/{version_id}", response_model=VersionDetail)
def get_version(project_id: UUID, version_id: UUID, user: CurrentUser, db: Database):
    return versions.get(db, project_id, version_id, user.id)


@router.put("/design-versions/{version_id}/review", response_model=VersionDetail)
def bind_review(project_id: UUID, version_id: UUID, data: PresentationPin, user: CurrentUser, db: Database,
                request_id: RequestID = None):
    return versions.bind_review(db, project_id, version_id, user.id, data, request_id=request_id)


@router.post("/design-branches", response_model=BranchRead, status_code=201)
def fork(project_id: UUID, data: BranchCreate, user: CurrentUser, db: Database, request_id: RequestID = None):
    return versions.create_branch(db, project_id, user.id, data, request_id=request_id)


@router.get("/design-branches", response_model=list[BranchRead])
def list_branches(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                  status: Optional[Literal["active", "promoted", "archived"]] = None, offset: Offset = 0, limit: Limit = 50):
    return versions.branches(db, project_id, event_id, user.id, status=status, offset=offset, limit=limit)


@router.get("/design-branches/{branch_id}", response_model=BranchRead)
def get_branch(project_id: UUID, branch_id: UUID, user: CurrentUser, db: Database):
    return versions.get_branch(db, project_id, branch_id, user.id)


@router.get("/design-branches/{branch_id}/versions", response_model=list[VersionRead])
def branch_history(project_id: UUID, branch_id: UUID, user: CurrentUser, db: Database, offset: Offset = 0, limit: Limit = 50):
    return versions.history(db, project_id, None, user.id, branch_id=branch_id, offset=offset, limit=limit)


@router.post("/design-branches/{branch_id}/versions", response_model=VersionDetail, status_code=201)
def revise_branch(project_id: UUID, branch_id: UUID, data: VersionWrite, user: CurrentUser, db: Database,
                  request_id: RequestID = None):
    return versions.write(db, project_id, None, user.id, data, branch_id=branch_id, request_id=request_id)


@router.get("/design-branches/{branch_id}/compare", response_model=VersionComparison)
def compare_branch(project_id: UUID, branch_id: UUID, user: CurrentUser, db: Database):
    return versions.compare_branch(db, project_id, branch_id, user.id)


@router.post("/design-branches/{branch_id}/restore", response_model=VersionDetail, status_code=201)
def restore_branch(project_id: UUID, branch_id: UUID, data: RestoreVersion, user: CurrentUser, db: Database,
                   request_id: RequestID = None):
    return versions.restore(db, project_id, user.id, data, branch_id=branch_id, request_id=request_id)


@router.post("/design-branches/{branch_id}/promote", response_model=VersionDetail, status_code=201)
def promote(project_id: UUID, branch_id: UUID, data: PromoteBranch, user: CurrentUser, db: Database,
            request_id: RequestID = None):
    return versions.promote(db, project_id, branch_id, user.id, data, request_id=request_id)


@router.post("/design-branches/{branch_id}/archive", response_model=BranchRead)
def archive(project_id: UUID, branch_id: UUID, data: ArchiveBranch, user: CurrentUser, db: Database,
            request_id: RequestID = None):
    return versions.archive(db, project_id, branch_id, user.id, data, request_id=request_id)


@router.get("/design-activity", response_model=list[ActivityRead])
def activity(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
             version_id: Optional[UUID] = None, branch_id: Optional[UUID] = None, offset: Offset = 0, limit: Limit = 50):
    return versions.activity(db, project_id, user.id, event_id=event_id, version_id=version_id, branch_id=branch_id,
                             offset=offset, limit=limit)
