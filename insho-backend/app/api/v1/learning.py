from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.learning import (
    ActualCreate, ActualEdit, ActualKind, ActualRead, ActualVerify, Comparison, Disposition, DispositionRead, Insight, InsightRequest,
    Metric, OutcomeStatus, Reconcile, ReportDetail, ReportRead, Source,
)
from app.services import learning_actuals, learning_reports


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/projects/{project_id}/learning", tags=["Learning"], dependencies=[Depends(private_response)])
UserDependency = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
RequestID = Annotated[Optional[UUID], Header(alias="X-Request-ID")]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("/actuals", response_model=ActualRead, status_code=201)
def create_actual(project_id: UUID, data: ActualCreate, user: UserDependency, db: Database, request_id: RequestID = None):
    return learning_actuals.create(db, project_id, user.id, data, request_id=request_id)


@router.get("/actuals", response_model=list[ActualRead])
def list_actuals(project_id: UUID, event_id: UUID, user: UserDependency, db: Database, kind: Optional[ActualKind] = None,
                 status: Optional[OutcomeStatus] = None, supplier_id: Optional[UUID] = None, offset: Offset = 0, limit: Limit = 50):
    return learning_actuals.history(db, project_id, user.id, event_id=event_id, kind=kind, status=status,
                                    supplier_id=supplier_id, offset=offset, limit=limit)


@router.get("/actuals/{actual_id}", response_model=ActualRead)
def read_actual(project_id: UUID, actual_id: UUID, user: UserDependency, db: Database):
    return learning_actuals.read(db, project_id, actual_id, user.id)


@router.patch("/actuals/{actual_id}", response_model=ActualRead)
def edit_actual(project_id: UUID, actual_id: UUID, data: ActualEdit, user: UserDependency, db: Database, request_id: RequestID = None):
    return learning_actuals.edit(db, project_id, actual_id, user.id, data, request_id=request_id)


@router.post("/actuals/{actual_id}/verify", response_model=ActualRead)
def verify_actual(project_id: UUID, actual_id: UUID, data: ActualVerify, user: UserDependency, db: Database, request_id: RequestID = None):
    return learning_actuals.verify(db, project_id, actual_id, user.id, data, request_id=request_id)


@router.delete("/actuals/{actual_id}", status_code=204)
def discard_actual(project_id: UUID, actual_id: UUID, user: UserDependency, db: Database,
                   expected_revision: int = Query(ge=1, lt=2147483647), request_id: RequestID = None):
    learning_actuals.discard(db, project_id, actual_id, user.id, expected_revision, request_id=request_id)
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})


@router.post("/reconciliations", response_model=ReportDetail, status_code=201)
def generate_reconciliation(project_id: UUID, data: Reconcile, user: UserDependency, db: Database, request_id: RequestID = None):
    return learning_reports.reconcile(db, project_id, user.id, data, request_id=request_id)


@router.get("/reconciliations", response_model=list[ReportRead])
def list_reconciliations(project_id: UUID, user: UserDependency, db: Database,
                         event_id: Optional[UUID] = None, offset: Offset = 0, limit: Limit = 50):
    return learning_reports.history(db, project_id, user.id, "reconciliation", event_id=event_id, offset=offset, limit=limit)


@router.get("/reconciliations/{report_id}", response_model=ReportDetail)
def read_reconciliation(project_id: UUID, report_id: UUID, user: UserDependency, db: Database):
    return learning_reports.read(db, project_id, "reconciliation", report_id, user.id)


@router.get("/reconciliations/{report_id}/comparisons", response_model=list[Comparison])
def comparisons(project_id: UUID, report_id: UUID, user: UserDependency, db: Database, metric: Optional[Metric] = None,
                offset: Offset = 0, limit: Limit = 50):
    report = learning_reports.read(db, project_id, "reconciliation", report_id, user.id)
    matching = [item for item in report.comparisons if metric is None or item.metric == metric]
    return matching[offset:offset + limit]


@router.post("/insights", response_model=ReportDetail, status_code=201)
async def generate_insights(project_id: UUID, data: InsightRequest, user: UserDependency, db: Database, request_id: RequestID = None):
    return await learning_reports.generate(db, project_id, user.id, data, request_id=request_id)


@router.get("/insights", response_model=list[ReportRead])
def list_insights(project_id: UUID, user: UserDependency, db: Database,
                  event_id: Optional[UUID] = None, offset: Offset = 0, limit: Limit = 50):
    return learning_reports.history(db, project_id, user.id, "insights", event_id=event_id, offset=offset, limit=limit)


@router.get("/insights/{report_id}", response_model=ReportDetail)
def insight_report(project_id: UUID, report_id: UUID, user: UserDependency, db: Database):
    return learning_reports.read(db, project_id, "insights", report_id, user.id)


@router.get("/insights/{report_id}/evidence", response_model=list[Source])
def evidence(project_id: UUID, report_id: UUID, user: UserDependency, db: Database, offset: Offset = 0, limit: Limit = 50):
    report = learning_reports.read(db, project_id, "insights", report_id, user.id)
    return report.evidence[offset:offset + limit]


@router.get("/insights/{report_id}/items/{insight_id}", response_model=Insight)
def read_insight(project_id: UUID, report_id: UUID, insight_id: UUID, user: UserDependency, db: Database):
    return learning_reports.insight(db, project_id, report_id, insight_id, user.id)


@router.post("/insights/{report_id}/items/{insight_id}/dispositions", response_model=DispositionRead, status_code=201)
def record_disposition(project_id: UUID, report_id: UUID, insight_id: UUID, data: Disposition, user: UserDependency, db: Database,
                       request_id: RequestID = None):
    return learning_reports.record_disposition(db, project_id, report_id, insight_id, user.id, data, request_id=request_id)


@router.get("/insights/{report_id}/items/{insight_id}/dispositions", response_model=list[DispositionRead])
def dispositions(project_id: UUID, report_id: UUID, insight_id: UUID, user: UserDependency, db: Database,
                 offset: Offset = 0, limit: Limit = 50):
    return learning_reports.dispositions(db, project_id, report_id, insight_id, user.id, offset=offset, limit=limit)
