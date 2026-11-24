from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.spatial_design import (
    ApplicationReceipt, ApplyRecommendation, IntegrationContext, Kind, Recommendation, RecommendationPage,
    RecommendationReview, RecommendationValidation, RejectRecommendation, Status, ValidateRecommendation,
)
from app.services import spatial_design


router = APIRouter(prefix="/spatial/scenes/{scene_id}/design-integration", tags=["Spatial Design Integration"])


@router.get("", response_model=IntegrationContext)
def integration_context(scene_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_design.context(db, scene_id, user.id)


@router.get("/recommendations", response_model=RecommendationPage)
def integration_recommendations(
    scene_id: UUID, source: Optional[Literal["plan", "elements"]] = None, kind: Optional[Kind] = None,
    status: Optional[Status] = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    return spatial_design.recommendations(db, scene_id, user.id, source=source, kind=kind, status=status,
                                          offset=offset, limit=limit)


@router.get("/recommendations/{recommendation_id}", response_model=Recommendation)
def integration_recommendation(scene_id: UUID, recommendation_id: UUID,
                               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_design.recommendation(db, scene_id, recommendation_id, user.id)


@router.post("/recommendations/{recommendation_id}/validate", response_model=RecommendationValidation)
def validate_recommendation(scene_id: UUID, recommendation_id: UUID, data: ValidateRecommendation,
                            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_design.validate_recommendation(db, scene_id, recommendation_id, user.id, data)


@router.post("/recommendations/{recommendation_id}/apply", response_model=ApplicationReceipt)
def apply_recommendation(scene_id: UUID, recommendation_id: UUID, data: ApplyRecommendation,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_design.apply_recommendation(db, scene_id, recommendation_id, user.id, data)


@router.post("/recommendations/{recommendation_id}/reject", response_model=RecommendationReview)
def reject_recommendation(scene_id: UUID, recommendation_id: UUID, data: RejectRecommendation,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_design.reject_recommendation(db, scene_id, recommendation_id, user.id, data)
