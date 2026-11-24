from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.presentation import (
    ApprovalDecision, ApprovalRequest, CheckpointCreate, CheckpointRead, PresentationCreate,
    PresentationRead, PresentationSummary, PresentationUpdate, PresentationValidation, SectionRead,
)
from app.services import presentation


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/projects/{project_id}/presentations", tags=["Insho Present"],
                   dependencies=[Depends(private_response)])
CurrentUser = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
Revision = Annotated[Optional[int], Query(ge=1, le=2147483647)]
RequestID = Annotated[Optional[UUID], Header(alias="X-Request-ID")]


@router.post("", response_model=PresentationRead, status_code=201)
def create(project_id: UUID, data: PresentationCreate, user: CurrentUser, db: Database, request_id: RequestID = None):
    return presentation.create(db, project_id, user.id, data, request_id=request_id)


@router.get("", response_model=list[PresentationSummary])
def list_presentations(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                       offset: Offset = 0, limit: Limit = 50):
    return presentation.list_presentations(db, project_id, user.id, event_id=event_id, offset=offset, limit=limit)


@router.get("/{presentation_id}", response_model=PresentationRead)
def get(project_id: UUID, presentation_id: UUID, user: CurrentUser, db: Database, revision: Revision = None):
    return presentation.get(db, project_id, presentation_id, user.id, version=revision)


@router.put("/{presentation_id}", response_model=PresentationRead)
def revise(project_id: UUID, presentation_id: UUID, data: PresentationUpdate, user: CurrentUser, db: Database,
           request_id: RequestID = None):
    return presentation.revise(db, project_id, presentation_id, user.id, data, request_id=request_id)


@router.get("/{presentation_id}/sections", response_model=list[SectionRead])
def sections(project_id: UUID, presentation_id: UUID, user: CurrentUser, db: Database, revision: Revision = None):
    return presentation.get(db, project_id, presentation_id, user.id, version=revision)["sections"]


@router.get("/{presentation_id}/revisions", response_model=list[PresentationSummary])
def history(project_id: UUID, presentation_id: UUID, user: CurrentUser, db: Database, offset: Offset = 0, limit: Limit = 50):
    return presentation.history(db, project_id, presentation_id, user.id, offset=offset, limit=limit)


@router.get("/{presentation_id}/alternatives", response_model=list[PresentationSummary])
def alternatives(project_id: UUID, presentation_id: UUID, user: CurrentUser, db: Database, offset: Offset = 0,
                 limit: Limit = 50):
    return presentation.alternatives(db, project_id, presentation_id, user.id, offset=offset, limit=limit)


@router.get("/{presentation_id}/validation", response_model=PresentationValidation)
def validate(project_id: UUID, presentation_id: UUID, user: CurrentUser, db: Database, revision: Revision = None):
    return presentation.validate(db, project_id, presentation_id, user.id, version=revision)


@router.post("/{presentation_id}/approvals", response_model=CheckpointRead, status_code=201)
def add_checkpoint(project_id: UUID, presentation_id: UUID, data: CheckpointCreate, user: CurrentUser, db: Database,
                   request_id: RequestID = None):
    return presentation.add_checkpoint(db, project_id, presentation_id, user.id, data, request_id=request_id)


@router.get("/{presentation_id}/approvals", response_model=list[CheckpointRead])
def approvals(project_id: UUID, presentation_id: UUID, user: CurrentUser, db: Database, revision: Revision = None,
              offset: Offset = 0, limit: Limit = 50):
    return presentation.approvals(db, project_id, presentation_id, user.id, version=revision, offset=offset, limit=limit)


@router.post("/{presentation_id}/approvals/{checkpoint_id}/request", response_model=CheckpointRead)
def request_approval(project_id: UUID, presentation_id: UUID, checkpoint_id: UUID, data: ApprovalRequest,
                     user: CurrentUser, db: Database, request_id: RequestID = None):
    return presentation.review(db, project_id, presentation_id, checkpoint_id, user.id, data, request_id=request_id)


@router.post("/{presentation_id}/approvals/{checkpoint_id}/decision", response_model=CheckpointRead)
def decide_approval(project_id: UUID, presentation_id: UUID, checkpoint_id: UUID, data: ApprovalDecision,
                    user: CurrentUser, db: Database, request_id: RequestID = None):
    return presentation.review(db, project_id, presentation_id, checkpoint_id, user.id, data, decide=True, request_id=request_id)
