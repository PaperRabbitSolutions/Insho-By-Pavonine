from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.production_procurement import (
    ApproveVersion, BudgetRead, BudgetWrite, OfferingRead, OfferingUpdate, OfferingWrite, ProcurementGenerate,
    ProcurementRead, ProcurementSelection, ProcurementStatus, ProcurementTransition, ProjectSupplierRead,
    ProjectSupplierWrite, ProposalDecision, ProposalRead, ProposalWrite, QuoteComparison, QuoteDecision, QuoteRead,
    QuoteStatus, QuoteWrite, SupplierRead, SupplierStatus, SupplierUpdate, SupplierWrite,
)
from app.services import (
    production_budgets as budgets, production_procurement as procurement, production_quotes as quotes,
    production_suppliers as suppliers, production_value_engineering as value_engineering,
)


router = APIRouter(prefix="/production", tags=["Production Sourcing"])
projects = APIRouter(prefix="/projects/{project_id}")
organizations = APIRouter(prefix="/organizations/{organization_id}/suppliers")
CurrentUser = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@organizations.post("", response_model=SupplierRead, status_code=201)
def create_supplier(organization_id: UUID, data: SupplierWrite, user: CurrentUser, db: Database):
    return suppliers.save_supplier(db, organization_id, user.id, data)


@organizations.get("", response_model=list[SupplierRead])
def list_suppliers(organization_id: UUID, user: CurrentUser, db: Database, status: Optional[SupplierStatus] = None,
                   offset: Offset = 0, limit: Limit = 50):
    return suppliers.list_suppliers(db, organization_id, user.id, status=status, offset=offset, limit=limit)


@organizations.get("/{supplier_id}", response_model=SupplierRead)
def get_supplier(organization_id: UUID, supplier_id: UUID, user: CurrentUser, db: Database):
    return suppliers.get_supplier(db, organization_id, supplier_id, user.id)


@organizations.put("/{supplier_id}", response_model=SupplierRead)
def update_supplier(organization_id: UUID, supplier_id: UUID, data: SupplierUpdate, user: CurrentUser, db: Database):
    return suppliers.save_supplier(db, organization_id, user.id, data, supplier_id=supplier_id)


@organizations.post("/{supplier_id}/offerings", response_model=OfferingRead, status_code=201)
def create_offering(organization_id: UUID, supplier_id: UUID, data: OfferingWrite, user: CurrentUser, db: Database):
    return suppliers.save_offering(db, organization_id, supplier_id, user.id, data)


@organizations.get("/{supplier_id}/offerings", response_model=list[OfferingRead])
def list_offerings(organization_id: UUID, supplier_id: UUID, user: CurrentUser, db: Database,
                   offset: Offset = 0, limit: Limit = 50):
    return suppliers.list_offerings(db, organization_id, supplier_id, user.id, offset=offset, limit=limit)


@organizations.put("/{supplier_id}/offerings/{offering_id}", response_model=OfferingRead)
def update_offering(organization_id: UUID, supplier_id: UUID, offering_id: UUID, data: OfferingUpdate,
                    user: CurrentUser, db: Database):
    return suppliers.save_offering(db, organization_id, supplier_id, user.id, data, offering_id=offering_id)


@projects.put("/suppliers/{supplier_id}", response_model=ProjectSupplierRead)
def link_supplier(project_id: UUID, supplier_id: UUID, data: ProjectSupplierWrite, user: CurrentUser, db: Database):
    return suppliers.link_supplier(db, project_id, supplier_id, user.id, data)


@projects.get("/suppliers", response_model=list[ProjectSupplierRead])
def project_suppliers(project_id: UUID, user: CurrentUser, db: Database, offset: Offset = 0, limit: Limit = 50):
    return suppliers.list_project_suppliers(db, project_id, user.id, offset=offset, limit=limit)


@projects.post("/budgets", response_model=BudgetRead, status_code=201)
def create_budget(project_id: UUID, data: BudgetWrite, user: CurrentUser, db: Database, event_id: Optional[UUID] = None):
    return budgets.save_budget(db, project_id, user.id, data, event_id=event_id)


@projects.get("/budgets", response_model=list[BudgetRead])
def list_budgets(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                 offset: Offset = 0, limit: Limit = 50):
    return budgets.list_budgets(db, project_id, user.id, event_id=event_id, offset=offset, limit=limit)


@projects.get("/budgets/{budget_id}", response_model=BudgetRead)
def get_budget(project_id: UUID, budget_id: UUID, user: CurrentUser, db: Database):
    return budgets.get_budget(db, project_id, budget_id, user.id)


@projects.post("/budgets/{budget_id}/approve", response_model=BudgetRead)
def approve_budget(project_id: UUID, budget_id: UUID, data: ApproveVersion, user: CurrentUser, db: Database):
    return budgets.approve_budget(db, project_id, budget_id, user.id, data)


@projects.post("/quotes", response_model=QuoteRead, status_code=201)
def create_quote(project_id: UUID, data: QuoteWrite, user: CurrentUser, db: Database, event_id: Optional[UUID] = None):
    return quotes.save_quote(db, project_id, user.id, data, event_id=event_id)


@projects.get("/quotes", response_model=list[QuoteRead])
def list_quotes(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                history: bool = False, status: Optional[QuoteStatus] = None, offset: Offset = 0, limit: Limit = 50):
    return quotes.list_quotes(db, project_id, user.id, event_id=event_id, history=history, status=status,
                              offset=offset, limit=limit)


@projects.get("/quotes/{quote_id}", response_model=QuoteRead)
def get_quote(project_id: UUID, quote_id: UUID, user: CurrentUser, db: Database):
    return quotes.get_quote(db, project_id, quote_id, user.id)


@projects.post("/quotes/{quote_id}/decision", response_model=QuoteRead)
def decide_quote(project_id: UUID, quote_id: UUID, data: QuoteDecision, user: CurrentUser, db: Database):
    return quotes.decide_quote(db, project_id, quote_id, user.id, data)


@projects.get("/boms/{bom_id}/quote-comparison", response_model=QuoteComparison)
def compare_quotes(project_id: UUID, bom_id: UUID, user: CurrentUser, db: Database, offset: Offset = 0, limit: Limit = 50):
    return quotes.compare_quotes(db, project_id, bom_id, user.id, offset=offset, limit=limit)


@projects.post("/procurements/generate", response_model=list[ProcurementRead])
def generate_requests(project_id: UUID, data: ProcurementGenerate, user: CurrentUser, db: Database,
                      event_id: Optional[UUID] = None):
    return procurement.generate(db, project_id, user.id, data, event_id=event_id)


@projects.get("/procurements", response_model=list[ProcurementRead])
def list_requests(project_id: UUID, user: CurrentUser, db: Database, event_id: Optional[UUID] = None,
                  status: Optional[ProcurementStatus] = None, offset: Offset = 0, limit: Limit = 50):
    return procurement.list_procurements(db, project_id, user.id, event_id=event_id, status=status, offset=offset, limit=limit)


@projects.get("/procurements/{request_id}", response_model=ProcurementRead)
def get_request(project_id: UUID, request_id: UUID, user: CurrentUser, db: Database):
    return procurement.get_procurement(db, project_id, request_id, user.id)


@projects.put("/procurements/{request_id}/selection", response_model=ProcurementRead)
def select_supplier(project_id: UUID, request_id: UUID, data: ProcurementSelection, user: CurrentUser, db: Database):
    return procurement.select_supplier(db, project_id, request_id, user.id, data)


@projects.post("/procurements/{request_id}/status", response_model=ProcurementRead)
def update_request_status(project_id: UUID, request_id: UUID, data: ProcurementTransition, user: CurrentUser, db: Database):
    return procurement.transition(db, project_id, request_id, user.id, data)


@projects.post("/procurements/{request_id}/proposals", response_model=ProposalRead, status_code=201)
def create_proposal(project_id: UUID, request_id: UUID, data: ProposalWrite, user: CurrentUser, db: Database):
    return value_engineering.create(db, project_id, request_id, user.id, data)


@projects.get("/procurements/{request_id}/proposals", response_model=list[ProposalRead])
def list_proposals(project_id: UUID, request_id: UUID, user: CurrentUser, db: Database,
                   offset: Offset = 0, limit: Limit = 50):
    return value_engineering.list_proposals(db, project_id, request_id, user.id, offset=offset, limit=limit)


@projects.get("/procurements/{request_id}/proposals/{proposal_id}", response_model=ProposalRead)
def get_proposal(project_id: UUID, request_id: UUID, proposal_id: UUID, user: CurrentUser, db: Database):
    return value_engineering.get_proposal(db, project_id, request_id, proposal_id, user.id)


@projects.post("/procurements/{request_id}/proposals/{proposal_id}/decision", response_model=ProposalRead)
def decide_proposal(project_id: UUID, request_id: UUID, proposal_id: UUID, data: ProposalDecision,
                    user: CurrentUser, db: Database):
    return value_engineering.decide(db, project_id, request_id, proposal_id, user.id, data)


router.include_router(organizations)
router.include_router(projects)
