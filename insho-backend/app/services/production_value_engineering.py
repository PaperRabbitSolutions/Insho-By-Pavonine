"""Proposed purchasing alternatives with separate review and application."""

from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.models.design_artifact import DesignArtifact
from app.models.production_procurement import ProductionValueProposal
from app.services import procurement_scope as scope, production, production_procurement as procurement
from app.services import production_quotes as quotes, production_suppliers as suppliers
from app.services.production_costing import money
from app.services.spatial import _missing


def _design_reference(db, project, event_id, artifact_id):
    if artifact_id is None:
        return None
    row = db.get(DesignArtifact, artifact_id)
    if row is None or row.project_id != project.id or (row.event_id is not None and row.event_id != event_id):
        _missing()
    return {"id": str(row.id), "module": row.module, "version": row.version, "status": row.status,
            "event_id": str(row.event_id) if row.event_id else None}


def _alternative(db, project, line, *, lock=False):
    material, definition = line["material"], line["definition"]
    current = {
        "material": suppliers.material_reference(db, project.organization_id, UUID(material["id"]), lock=lock)
        if material is not None else None,
        "definition": suppliers.component_reference(db, project.organization_id, UUID(definition["id"]),
                                                    project=project, lock=lock) if definition is not None else None,
    }
    if any(value is not None and value["status"] != "active" for value in current.values()):
        production._conflict("proposal_alternative_unavailable", "The proposed alternative is no longer active.")
    return current


def _proposal_view(db, project, request, row, boms):
    source = boms.get(procurement._bom_id(request))
    reasons = ["bom_changed"] if source is None or not scope.bom_ready(source[2], approved=True) else []
    quote = quotes.quotation(db, project, row.quotation_id)
    view = quotes.quote_view(db, project, quote, boms)
    if view["stale"] or not view["latest"] or view["expired"]:
        reasons.append("quotation_changed")
    alternative = row.proposal["alternative"]
    if ((alternative["material"] is not None and row.material_id is None)
            or (alternative["definition"] is not None and row.definition_id is None)):
        reasons.append("alternative_missing")
    else:
        try:
            if _alternative(db, project, alternative) != alternative:
                reasons.append("alternative_changed")
        except HTTPException as exc:
            if exc.status_code not in (404, 409):
                raise
            reasons.append("alternative_unavailable")
    if _design_changed(db, row):
        reasons.append("design_reference_changed")
    if row.status == "applied" and (request.selection.get("substitution") or {}).get("proposal_id") != str(row.id):
        reasons.append("application_replaced")
    return {
        "id": row.id, "procurement_id": row.procurement_id, "quotation_id": row.quotation_id,
        "material_id": row.material_id, "definition_id": row.definition_id, "design_artifact_id": row.design_artifact_id,
        "status": row.status, "version": row.version, "proposal": row.proposal, "audit": row.audit,
        "created_at": row.created_at, "stale": bool(reasons), "stale_reasons": reasons,
    }


def _design_changed(db, row):
    reference = row.proposal["design_artifact"]
    if reference is None:
        return False
    design = db.get(DesignArtifact, row.design_artifact_id) if row.design_artifact_id else None
    return design is None or design.status == "archived" or design.version != reference["version"]


def applied_source(db, request):
    """Resolve the effective applied proposal in the request's scope."""
    snapshot = request.selection.get("substitution")
    if not snapshot or request.status == "cancelled":
        return None
    row = db.get(ProductionValueProposal, UUID(snapshot["proposal_id"]))
    if (row is None or row.procurement_id != request.id or row.quotation_id != request.quotation_id
            or row.status != "applied" or row.version != snapshot["proposal_version"] or _design_changed(db, row)):
        return None
    return {"procurement_id": str(request.id), "proposal_id": str(row.id), "proposal_version": row.version}


def create(db, project_id, request_id, user_id, data):
    project = scope.project_scope(db, project_id, user_id, write=True)
    request = procurement.procurement(db, project, request_id)
    scope.event_scope(db, project, user_id, request.event_id)
    scope.check_version(data.expected_procurement_version, request.version, "procurement")
    procurement.require_draft(request)
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    procurement.require_current_bom(request, boms)
    quote = quotes.quotation(db, project, data.quotation_id)
    suppliers.supplier_for_project(db, project, quote.supplier_id, lock=True)
    quotes.require_commercial_quote(db, project, quote, boms)
    line = procurement.quotation_line(request, quote)
    alternative = _alternative(db, project, line, lock=True)
    material, definition = alternative["material"], alternative["definition"]
    original = request.requirement
    if not ((material is not None and material["id"] != original["material_id"])
            or (definition is not None and definition["id"] != original["definition_id"])):
        production._invalid("The quotation line must specify a different catalogue material or component.")
    if alternative != {"material": line["material"], "definition": line["definition"]}:
        production._conflict("proposal_quotation_alternative_changed", "Record a new quote revision for the changed catalogue alternative.")
    cost = procurement.quote_line_cost(quote, line)
    with localcontext() as context:
        context.prec = 60
        difference = money(cost - Decimal(original["estimated_cost"]), quote.currency_precision)
    design = _design_reference(db, project, request.event_id, data.design_artifact_id)
    proposal = {
        **data.model_dump(mode="json", exclude={"expected_procurement_version", "quotation_id", "design_artifact_id"}),
        "original": original, "alternative": alternative, "quoted_line": line,
        "alternative_cost": str(cost), "cost_difference": str(difference), "currency": quote.currency,
        "quotation_version": quote.version, "design_artifact": design,
    }
    row = ProductionValueProposal(
        id=uuid4(), procurement_id=request.id, quotation_id=quote.id, version=1, status="proposed",
        material_id=UUID(material["id"]) if material is not None else None,
        definition_id=UUID(definition["id"]) if definition is not None else None, design_artifact_id=data.design_artifact_id,
        proposal=proposal, audit=[scope.audit(user_id, "proposed", procurement_version=request.version)],
    )
    db.add(row)
    scope.flush(db)
    result = _proposal_view(db, project, request, row, boms)
    production._commit_changes(db)
    return result


def _get(db, project, request_id, proposal_id):
    request = procurement.procurement(db, project, request_id)
    row = production._scoped_row(db, ProductionValueProposal, proposal_id, ProductionValueProposal.procurement_id == request.id)
    return request, row


def decide(db, project_id, request_id, proposal_id, user_id, data):
    project = scope.project_scope(db, project_id, user_id, write=True)
    scope.require_approver(db, user_id, project)
    request, row = _get(db, project, request_id, proposal_id)
    scope.event_scope(db, project, user_id, request.event_id)
    scope.check_version(data.expected_version, row.version, "proposal")
    scope.check_version(data.expected_procurement_version, request.version, "procurement")
    allowed = {"proposed": {"approved", "rejected"}, "approved": {"applied", "rejected"}}
    if data.decision not in allowed.get(row.status, ()):
        production._conflict("proposal_transition_invalid", "The proposed decision is unavailable for this revision.")
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    if data.decision != "rejected":
        procurement.require_draft(request)
        procurement.require_current_bom(request, boms)
        if not row.proposal["evidence_reference"]:
            production._conflict("proposal_evidence_required", "Supply the supporting review evidence in a proposal.")
        quote = quotes.quotation(db, project, row.quotation_id)
        suppliers.supplier_for_project(db, project, quote.supplier_id, lock=True)
        quotes.require_commercial_quote(db, project, quote, boms, approved=True)
        line = procurement.quotation_line(request, quote)
        alternative = _alternative(db, project, line, lock=True)
        view = _proposal_view(db, project, request, row, boms)
        if view["stale"] or alternative != row.proposal["alternative"]:
            production._conflict("proposal_stale", "Review the changed sources in a new proposal.", reasons=view["stale_reasons"])
        conditions = procurement._supplier_conditions(line, quote)
        procurement.validate_order_quantity(line, conditions, accept_difference=True)
        if data.decision == "applied":
            previous = (request.selection.get("substitution") or {}).get("proposal_id")
            replacement = str(data.replaces_proposal_id) if data.replaces_proposal_id else None
            if previous != replacement:
                production._conflict("proposal_replacement_conflict", "Confirm the current applied proposal to replace.",
                                     current_proposal_id=previous)
            request.selected_supplier_id, request.quotation_id = quote.supplier_id, quote.id
            request.selection = {
                **request.selection, **conditions, "accept_quantity_difference": True,
                "substitution": {"proposal_id": str(row.id), "proposal_version": row.version + 1,
                                 "alternative": alternative, "quote_id": str(quote.id),
                                 "evidence_reference": row.proposal["evidence_reference"]},
            }
            request.version += 1
            request.status = "sourcing"
            request.audit = [*request.audit, scope.audit(user_id, "substitution_applied", proposal_id=str(row.id),
                                                        quotation_id=str(quote.id), version=request.version,
                                                        replaces_proposal_id=replacement)]
    row.status, row.version = data.decision, row.version + 1
    row.audit = [*row.audit, scope.audit(user_id, row.status, note=data.note, procurement_version=request.version)]
    scope.flush(db)
    result = _proposal_view(db, project, request, row, boms)
    production._commit_changes(db)
    production._log("value_proposal_decided", proposal_id=str(proposal_id), decision=data.decision)
    return result


def get_proposal(db, project_id, request_id, proposal_id, user_id):
    project = scope.project_scope(db, project_id, user_id)
    request, row = _get(db, project, request_id, proposal_id)
    scope.event_scope(db, project, user_id, request.event_id)
    return _proposal_view(db, project, request, row, scope.BOMSources(db, project, user_id))


def list_proposals(db, project_id, request_id, user_id, *, offset, limit):
    project = scope.project_scope(db, project_id, user_id)
    request = procurement.procurement(db, project, request_id)
    scope.event_scope(db, project, user_id, request.event_id)
    rows = db.scalars(select(ProductionValueProposal).where(ProductionValueProposal.procurement_id == request.id)
                      .order_by(ProductionValueProposal.created_at, ProductionValueProposal.id).offset(offset).limit(limit))
    boms = scope.BOMSources(db, project, user_id)
    return [_proposal_view(db, project, request, row, boms) for row in rows]
