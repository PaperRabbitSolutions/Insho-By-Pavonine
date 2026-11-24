from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.design_intelligence import (
    ArtifactResponse, ArtifactUpdate, ElementResponse, ElementUpdate,
    GenerateDesignRequest,
)
from app.services.design_intelligence import DesignIntelligenceService

router = APIRouter(prefix="/design-intelligence", tags=["Design Intelligence"])
MODULES = {"mood", "style", "plan", "vision", "elements"}


@router.post("/projects/{project_id}/{module}/generate", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def generate_module(
    project_id: UUID,
    module: str,
    data: GenerateDesignRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await DesignIntelligenceService.generate(db, project_id, module, data, current_user.id)


@router.get("/projects/{project_id}/{module}", response_model=list[ArtifactResponse])
def list_module(
    project_id: UUID,
    module: str,
    event_id: Optional[UUID] = None,
    history: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return DesignIntelligenceService.list_artifacts(db, project_id, module, current_user.id, event_id, history)


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(artifact_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    artifact = DesignIntelligenceService.get_artifact(db, artifact_id, current_user.id)
    return DesignIntelligenceService._response(artifact, DesignIntelligenceService._is_stale(db, artifact, current_user.id))


@router.put("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def update_artifact(artifact_id: UUID, data: ArtifactUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return DesignIntelligenceService.update_artifact(db, artifact_id, data, current_user.id)


@router.post("/artifacts/{artifact_id}/approve", response_model=ArtifactResponse)
def approve_artifact(artifact_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return DesignIntelligenceService.approve(db, artifact_id, current_user.id)


@router.get("/artifacts/{artifact_id}/elements", response_model=list[ElementResponse])
def list_elements(artifact_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return DesignIntelligenceService.list_elements(db, artifact_id, current_user.id)


@router.patch("/elements/{element_id}", response_model=ElementResponse)
def update_element(element_id: UUID, data: ElementUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return DesignIntelligenceService.update_element(db, element_id, data.confirmed, data.details, current_user.id)
