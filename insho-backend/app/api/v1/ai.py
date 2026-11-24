from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.models.ai_execution import AIExecution
from app.schemas.ai import AIExecutionRequest, AIExecutionResponse
from app.services.ai_orchestration import ai_execution_service
from app.security import require_project_access

router = APIRouter(prefix="/ai", tags=["AI"])
execution_service = ai_execution_service


@router.post("/execute", response_model=AIExecutionResponse)
async def execute_ai(
    request: AIExecutionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await execution_service.execute(db, request, current_user.id)


@router.get("/projects/{project_id}/executions", response_model=list[AIExecutionResponse])
def list_project_executions(
    project_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_project_access(db, current_user.id, project_id)
    records = db.scalars(
        select(AIExecution)
        .where(AIExecution.project_id == project_id)
        .order_by(AIExecution.created_at.desc())
        .limit(limit)
    ).all()
    return [execution_service._response(record) for record in records]
