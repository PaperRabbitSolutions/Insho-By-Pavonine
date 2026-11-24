from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.design_audit import (
    AuditComparison, AuditCreate, AuditDetail, AuditOptions, AuditRead, AuditStatus, Dimension, DimensionResult, Evidence, Recommendation,
)
from app.services import design_audit


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/projects/{project_id}/design-audits", tags=["Design Audit"], dependencies=[Depends(private_response)])
UserDependency = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
RequestID = Annotated[Optional[UUID], Header(alias="X-Request-ID")]


@router.post("", response_model=AuditDetail, status_code=201)
async def start(project_id: UUID, data: AuditCreate, user: UserDependency, db: Database, request_id: RequestID = None):
    options = AuditOptions.model_validate(data.model_dump(exclude={"design_version_id"}))
    return await design_audit.start(db, project_id, data.design_version_id, user.id, options, request_id=request_id)


@router.get("", response_model=list[AuditRead])
def history(project_id: UUID, user: UserDependency, db: Database, version_id: Optional[UUID] = None, event_id: Optional[UUID] = None,
            status: Optional[AuditStatus] = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return design_audit.history(db, project_id, user.id, version_id=version_id, event_id=event_id, status=status, offset=offset, limit=limit)


@router.get("/compare", response_model=AuditComparison)
def compare(project_id: UUID, from_evaluation_id: UUID, to_evaluation_id: UUID, user: UserDependency, db: Database):
    return design_audit.compare(db, project_id, from_evaluation_id, to_evaluation_id, user.id)


@router.get("/{evaluation_id}", response_model=AuditDetail)
def read(project_id: UUID, evaluation_id: UUID, user: UserDependency, db: Database):
    return design_audit.get(db, project_id, evaluation_id, user.id)


@router.get("/{evaluation_id}/dimensions", response_model=list[DimensionResult])
def results(project_id: UUID, evaluation_id: UUID, user: UserDependency, db: Database):
    return design_audit.results(db, project_id, evaluation_id, user.id)


@router.get("/{evaluation_id}/dimensions/{dimension}", response_model=DimensionResult)
def dimension_result(project_id: UUID, evaluation_id: UUID, dimension: Dimension, user: UserDependency, db: Database):
    return design_audit.results(db, project_id, evaluation_id, user.id, dimension=dimension)[0]


@router.get("/{evaluation_id}/evidence", response_model=list[Evidence])
def evidence(project_id: UUID, evaluation_id: UUID, user: UserDependency, db: Database, dimension: Optional[Dimension] = None):
    return design_audit.evidence_items(db, project_id, evaluation_id, user.id, dimension=dimension)


@router.get("/{evaluation_id}/recommendations", response_model=list[Recommendation])
def recommendations(project_id: UUID, evaluation_id: UUID, user: UserDependency, db: Database, dimension: Optional[Dimension] = None):
    return design_audit.recommendations(db, project_id, evaluation_id, user.id, dimension=dimension)


@router.post("/{evaluation_id}/retry", response_model=AuditDetail, status_code=201)
async def retry(project_id: UUID, evaluation_id: UUID, user: UserDependency, db: Database, request_id: RequestID = None):
    return await design_audit.retry(db, project_id, evaluation_id, user.id, request_id=request_id)
