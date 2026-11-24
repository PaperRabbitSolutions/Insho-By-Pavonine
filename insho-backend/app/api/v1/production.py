from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.production import (
    AssemblyEntry, ItemCreate, ItemUpdate, MaterialWrite, MeasurementWrite, Mutation,
    RecordCreate, RecordDetail, RecordRead, RecordUpdate, SourceReview, SpecificationRead, SpecificationWrite,
)
from app.schemas.production_bom import BOMApprove, BOMDetail, BOMRead, BOMWrite, ProjectCostRead
from app.schemas.production_materials import (
    MaterialAssignmentRead, MaterialDefinitionInput, MaterialDefinitionRead, MaterialDefinitionUpdate, MaterialStatus,
)
from app.schemas.production_quantities import CalculateQuantities, CalculationResult, QuantityRead
from app.schemas.production_workflow import WorkflowPlanWrite, WorkflowRead, WorkflowRevisionRead, WorkflowTransition
from app.services import production, production_bom, production_materials, production_quantities, production_specifications
from app.services import production_workflow
from app.services.production_units import MeasurementKind


router = APIRouter(prefix="/production", tags=["Production"])
records = APIRouter(prefix="/scenes/{scene_id}/records")
CurrentUser = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
ExpectedVersionQuery = Annotated[int, Query(gt=0, le=2147483647)]


@records.post("", response_model=RecordRead, status_code=201)
def create_record(scene_id: UUID, data: RecordCreate, user: CurrentUser, db: Database):
    return production.create_record(db, scene_id, user.id, data)


@records.get("", response_model=list[RecordRead])
def list_records(scene_id: UUID, user: CurrentUser, db: Database,
                 offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return production.list_records(db, scene_id, user.id, offset=offset, limit=limit)


@records.get("/{record_id}", response_model=RecordDetail)
def get_record(scene_id: UUID, record_id: UUID, user: CurrentUser, db: Database):
    return production.get_record(db, scene_id, record_id, user.id)


@records.patch("/{record_id}", response_model=RecordRead)
def rename_record(scene_id: UUID, record_id: UUID, data: RecordUpdate, user: CurrentUser, db: Database):
    return production.rename_record(db, scene_id, record_id, user.id, data)


@records.delete("/{record_id}", status_code=204)
def delete_record(scene_id: UUID, record_id: UUID, expected_version: ExpectedVersionQuery,
                  user: CurrentUser, db: Database):
    production.delete_record(db, scene_id, record_id, user.id, expected_version)
    return Response(status_code=204)


@records.post("/{record_id}/review-sources", response_model=RecordRead)
def review_sources(scene_id: UUID, record_id: UUID, data: SourceReview, user: CurrentUser, db: Database):
    return production.review_sources(db, scene_id, record_id, user.id, data)


@records.post("/{record_id}/approve", response_model=RecordRead)
def approve_record(scene_id: UUID, record_id: UUID, data: SourceReview, user: CurrentUser, db: Database):
    return production.review_sources(db, scene_id, record_id, user.id, data, approve=True)


@records.get("/{record_id}/workflow", response_model=WorkflowRead)
def get_workflow(scene_id: UUID, record_id: UUID, user: CurrentUser, db: Database):
    return production_workflow.get(db, scene_id, record_id, user.id)


@records.put("/{record_id}/workflow/plan", response_model=WorkflowRead)
def save_workflow_plan(scene_id: UUID, record_id: UUID, data: WorkflowPlanWrite, user: CurrentUser, db: Database):
    return production_workflow.save_plan(db, scene_id, record_id, user.id, data)


@records.post("/{record_id}/workflow/status", response_model=WorkflowRead)
def transition_workflow(scene_id: UUID, record_id: UUID, data: WorkflowTransition, user: CurrentUser, db: Database):
    return production_workflow.transition(db, scene_id, record_id, user.id, data)


@records.get("/{record_id}/workflow/history", response_model=list[WorkflowRevisionRead])
def workflow_history(scene_id: UUID, record_id: UUID, user: CurrentUser, db: Database,
                     offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return production_workflow.history(db, scene_id, record_id, user.id, offset=offset, limit=limit)


@records.post("/{record_id}/items", response_model=Mutation, status_code=201)
def create_item(scene_id: UUID, record_id: UUID, data: ItemCreate, user: CurrentUser, db: Database):
    return production.write_item(db, scene_id, record_id, user.id, data)


@records.put("/{record_id}/items/{item_id}", response_model=Mutation)
def update_item(scene_id: UUID, record_id: UUID, item_id: UUID, data: ItemUpdate, user: CurrentUser, db: Database):
    return production.write_item(db, scene_id, record_id, user.id, data, item_id=item_id)


@records.delete("/{record_id}/items/{item_id}", response_model=Mutation)
def delete_item(scene_id: UUID, record_id: UUID, item_id: UUID, expected_version: ExpectedVersionQuery,
                user: CurrentUser, db: Database):
    return production.delete_item(db, scene_id, record_id, item_id, user.id, expected_version)


@records.post("/{record_id}/materials", response_model=Mutation, status_code=201)
def create_material(scene_id: UUID, record_id: UUID, data: MaterialWrite, user: CurrentUser, db: Database):
    return production.write_material(db, scene_id, record_id, user.id, data)


@records.put("/{record_id}/materials/{material_id}", response_model=Mutation)
def update_material(scene_id: UUID, record_id: UUID, material_id: UUID, data: MaterialWrite,
                    user: CurrentUser, db: Database):
    return production.write_material(db, scene_id, record_id, user.id, data, material_id=material_id)


@records.delete("/{record_id}/materials/{material_id}", response_model=Mutation)
def delete_material(scene_id: UUID, record_id: UUID, material_id: UUID, expected_version: ExpectedVersionQuery,
                    user: CurrentUser, db: Database):
    return production.delete_material(db, scene_id, record_id, material_id, user.id, expected_version)


@records.post("/{record_id}/items/{item_id}/specifications", response_model=Mutation, status_code=201)
def create_specification(scene_id: UUID, record_id: UUID, item_id: UUID, data: SpecificationWrite,
                         user: CurrentUser, db: Database):
    return production.write_specification(db, scene_id, record_id, item_id, user.id, data)


@records.put("/{record_id}/items/{item_id}/specifications/{specification_id}", response_model=Mutation)
def update_specification(scene_id: UUID, record_id: UUID, item_id: UUID, specification_id: UUID,
                         data: SpecificationWrite, user: CurrentUser, db: Database):
    return production.write_specification(db, scene_id, record_id, item_id, user.id, data,
                                          specification_id=specification_id)


@records.delete("/{record_id}/items/{item_id}/specifications/{specification_id}", response_model=Mutation)
def delete_specification(scene_id: UUID, record_id: UUID, item_id: UUID, specification_id: UUID,
                         expected_version: ExpectedVersionQuery, user: CurrentUser, db: Database):
    return production.delete_specification(db, scene_id, record_id, item_id, specification_id, user.id, expected_version)


@records.put("/{record_id}/items/{item_id}/measurements/{kind}", response_model=Mutation)
def write_measurement(scene_id: UUID, record_id: UUID, item_id: UUID, kind: MeasurementKind,
                      data: MeasurementWrite, user: CurrentUser, db: Database):
    return production.write_measurement(db, scene_id, record_id, item_id, kind, user.id, data)


@records.delete("/{record_id}/items/{item_id}/measurements/{kind}", response_model=Mutation)
def delete_measurement(scene_id: UUID, record_id: UUID, item_id: UUID, kind: MeasurementKind,
                       expected_version: ExpectedVersionQuery, user: CurrentUser, db: Database):
    return production.delete_measurement(db, scene_id, record_id, item_id, kind, user.id, expected_version)


@records.get("/{record_id}/items/{item_id}/specifications", response_model=list[SpecificationRead])
def list_specifications(scene_id: UUID, record_id: UUID, item_id: UUID, user: CurrentUser, db: Database):
    return production_specifications.list_for_item(db, scene_id, record_id, item_id, user.id)


@records.get("/{record_id}/items/{item_id}/specifications/{specification_id}", response_model=SpecificationRead)
def get_specification(scene_id: UUID, record_id: UUID, item_id: UUID, specification_id: UUID,
                      user: CurrentUser, db: Database):
    return production_specifications.detail(db, scene_id, record_id, item_id, specification_id, user.id)


@records.post("/{record_id}/items/{item_id}/specifications/{specification_id}/approve", response_model=Mutation)
def approve_specification(scene_id: UUID, record_id: UUID, item_id: UUID, specification_id: UUID,
                          data: SourceReview, user: CurrentUser, db: Database):
    return production_specifications.approve(db, scene_id, record_id, item_id, specification_id, user.id, data)


@records.post("/{record_id}/quantities/calculate", response_model=CalculationResult)
def calculate_quantities(scene_id: UUID, record_id: UUID, data: CalculateQuantities, user: CurrentUser, db: Database):
    return production_quantities.calculate(db, scene_id, record_id, user.id, data)


@records.get("/{record_id}/quantities", response_model=list[QuantityRead])
def list_quantities(scene_id: UUID, record_id: UUID, user: CurrentUser, db: Database, history: bool = False,
                    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return production_quantities.list_for_record(db, scene_id, record_id, user.id, history=history,
                                                 offset=offset, limit=limit)


@records.get("/{record_id}/quantities/{quantity_id}", response_model=QuantityRead)
def get_quantity(scene_id: UUID, record_id: UUID, quantity_id: UUID, user: CurrentUser, db: Database):
    return production_quantities.detail(db, scene_id, record_id, quantity_id, user.id)


@records.post("/{record_id}/quantities/{quantity_id}/recalculate", response_model=CalculationResult)
def recalculate_quantity(scene_id: UUID, record_id: UUID, quantity_id: UUID, data: SourceReview,
                         user: CurrentUser, db: Database):
    return production_quantities.recalculate(db, scene_id, record_id, quantity_id, user.id, data)


@router.get("/projects/{project_id}/quantities", response_model=list[QuantityRead])
def project_quantities(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                       history: bool = False, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return production_quantities.list_for_project(db, project_id, user.id, event_id=event_id, history=history,
                                                  offset=offset, limit=limit)


@router.post("/organizations/{organization_id}/materials", response_model=MaterialDefinitionRead, status_code=201)
def create_material_definition(organization_id: UUID, data: MaterialDefinitionInput, user: CurrentUser, db: Database):
    return production_materials.save_definition(db, organization_id, user.id, data)


@router.get("/organizations/{organization_id}/materials", response_model=list[MaterialDefinitionRead])
def list_material_definitions(organization_id: UUID, user: CurrentUser, db: Database,
                              status: Optional[MaterialStatus] = None, offset: int = Query(0, ge=0),
                              limit: int = Query(50, ge=1, le=100)):
    return production_materials.list_definitions(db, organization_id, user.id, status=status, offset=offset, limit=limit)


@router.get("/organizations/{organization_id}/materials/{definition_id}", response_model=MaterialDefinitionRead)
def get_material_definition(organization_id: UUID, definition_id: UUID, user: CurrentUser, db: Database,
                            version: Optional[int] = Query(None, gt=0, le=2147483647)):
    return production_materials.get_definition(db, organization_id, definition_id, user.id, version=version)


@router.put("/organizations/{organization_id}/materials/{definition_id}", response_model=MaterialDefinitionRead)
def update_material_definition(organization_id: UUID, definition_id: UUID, data: MaterialDefinitionUpdate,
                               user: CurrentUser, db: Database):
    return production_materials.save_definition(db, organization_id, user.id, data, definition_id=definition_id)


@records.get("/{record_id}/materials/{material_id}", response_model=MaterialAssignmentRead)
def material_assignment(scene_id: UUID, record_id: UUID, material_id: UUID, user: CurrentUser, db: Database):
    return production_materials.get_assignment(db, scene_id, record_id, material_id, user.id)


@records.get("/{record_id}/items/{item_id}/breakdown", response_model=list[AssemblyEntry])
def assembly_breakdown(scene_id: UUID, record_id: UUID, item_id: UUID, user: CurrentUser, db: Database):
    return production.assembly_breakdown(db, scene_id, record_id, item_id, user.id)


@records.post("/{record_id}/boms", response_model=BOMDetail)
def revise_bom(scene_id: UUID, record_id: UUID, data: BOMWrite, user: CurrentUser, db: Database):
    return production_bom.revise(db, scene_id, record_id, user.id, data)


@records.get("/{record_id}/boms", response_model=list[BOMRead])
def list_boms(scene_id: UUID, record_id: UUID, user: CurrentUser, db: Database,
              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return production_bom.list_revisions(db, scene_id, record_id, user.id, offset=offset, limit=limit)


@records.get("/{record_id}/boms/{bom_id}", response_model=BOMDetail)
def get_bom(scene_id: UUID, record_id: UUID, bom_id: UUID, user: CurrentUser, db: Database):
    return production_bom.get_revision(db, scene_id, record_id, bom_id, user.id)


@records.post("/{record_id}/boms/{bom_id}/approve", response_model=BOMDetail)
def approve_bom(scene_id: UUID, record_id: UUID, bom_id: UUID, data: BOMApprove, user: CurrentUser, db: Database):
    return production_bom.approve(db, scene_id, record_id, bom_id, user.id, data)


@router.get("/projects/{project_id}/cost-summary", response_model=ProjectCostRead)
def project_cost_summary(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                         record_id: Optional[list[UUID]] = Query(None)):
    return production_bom.project_costs(db, project_id, user.id, event_id=event_id, record_ids=record_id)


router.include_router(records)
