from uuid import UUID
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.context import (
    EventContextResponse,
    ProjectContextResponse,
    VenueContextResponse,
)
from app.schemas.ai import AIExecutionRequest
from app.schemas.context import ProjectContextResponse
from app.services.ai_orchestration import ai_execution_service
from app.services.context import ContextService


router = APIRouter(prefix="/context", tags=["Context"])


@router.get("/projects/{project_id}", response_model=ProjectContextResponse)
def get_project_context(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ContextService.get_project_context(db, project_id, current_user.id)


@router.post("/projects/{project_id}/summarize")
async def summarize_project_context(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # This feature uses the existing authorized context boundary, then calls only
    # the shared orchestration service. It does not load assets or unrelated projects.
    context = ContextService.get_project_context(db, project_id, current_user.id)
    context_payload = ProjectContextResponse.model_validate(context).model_dump(mode="json")
    serialized_context = json.dumps(context_payload, separators=(",", ":"), ensure_ascii=False)
    if len(serialized_context) > 100_000:
        raise HTTPException(status_code=413, detail="Project context is too large for AI summarization")
    request = AIExecutionRequest(
        task="project_context_summary",
        input=serialized_context,
        instructions=(
            "Summarize the supplied INSHO project context for an event design team. "
            "Use only facts present in the context, call out missing or conflicting details, "
            "and do not calculate or invent geometry, quantities, budgets, or costs."
        ),
        required_capabilities=["text"],
        project_id=project_id,
        feature="project_context",
    )
    return await ai_execution_service.execute(db, request, current_user.id)


@router.get("/events/{event_id}", response_model=EventContextResponse)
def get_event_context(
    event_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ContextService.get_event_context(db, event_id, current_user.id)


@router.get("/venues/{venue_id}", response_model=VenueContextResponse)
def get_venue_context(
    venue_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ContextService.get_venue_context(db, venue_id, current_user.id)
