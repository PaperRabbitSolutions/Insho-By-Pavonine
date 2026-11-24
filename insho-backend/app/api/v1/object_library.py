"""Tenant-scoped object catalog routes."""

from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.object_library import (
    Availability, CatalogFacets, CatalogFilters, CatalogPage, DefinitionCreate, DefinitionDetail,
    DefinitionUpdate, DuplicateDefinition, Scope,
)
from app.services import object_library


router = APIRouter(prefix="/object-library/organizations/{organization_id}", tags=["Object Library"])


def catalog_query(
    workspace_id: Optional[UUID] = None, project_id: Optional[UUID] = None, scope: Optional[Scope] = None,
    status_filter: Literal["active", "archived", "all"] = Query(default="active", alias="status"),
    q: Optional[str] = Query(default=None, min_length=1, max_length=200),
    category: Optional[str] = None, object_type: Optional[str] = None,
    tags: list[str] = Query(default=[]), geometry_type: Optional[str] = None,
    plan_compatible: Optional[bool] = None, asset_availability: Optional[Availability] = None,
    min_width_m: Optional[Decimal] = None, max_width_m: Optional[Decimal] = None,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
):
    try:
        return CatalogFilters(workspace_id=workspace_id, project_id=project_id, scope=scope, status=status_filter,
                              q=q, category=category, object_type=object_type, tags=tags, geometry_type=geometry_type,
                              plan_compatible=plan_compatible, asset_availability=asset_availability,
                              min_width_m=min_width_m, max_width_m=max_width_m, offset=offset, limit=limit)
    except ValidationError as exc:
        raise HTTPException(422, detail="Invalid catalog filters.") from exc


@router.get("/definitions", response_model=CatalogPage)
def list_definitions(organization_id: UUID, filters: CatalogFilters = Depends(catalog_query),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return object_library.list_definitions(db, user.id, organization_id, filters)


@router.get("/facets", response_model=CatalogFacets)
def list_facets(organization_id: UUID, filters: CatalogFilters = Depends(catalog_query),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return object_library.catalog_facets(db, user.id, organization_id, filters)


@router.post("/definitions", response_model=DefinitionDetail, status_code=status.HTTP_201_CREATED)
def create_definition(organization_id: UUID, data: DefinitionCreate,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return object_library.create_definition(db, user.id, organization_id, data)


@router.get("/definitions/{definition_id}", response_model=DefinitionDetail)
def get_definition(organization_id: UUID, definition_id: UUID,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return object_library.get_definition(db, user.id, organization_id, definition_id)


@router.patch("/definitions/{definition_id}", response_model=DefinitionDetail)
def update_definition(organization_id: UUID, definition_id: UUID, data: DefinitionUpdate,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return object_library.update_definition(db, user.id, organization_id, definition_id, data)


@router.delete("/definitions/{definition_id}", response_model=DefinitionDetail)
def archive_definition(organization_id: UUID, definition_id: UUID, expected_version: int = Query(ge=1),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return object_library.archive_definition(db, user.id, organization_id, definition_id, expected_version)


@router.post("/definitions/{definition_id}/duplicate", response_model=DefinitionDetail, status_code=status.HTTP_201_CREATED)
def duplicate_definition(organization_id: UUID, definition_id: UUID, data: DuplicateDefinition,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return object_library.duplicate_definition(db, user.id, organization_id, definition_id, data)
