"""Purchasing requirements and explicitly authorized internal decisions."""

from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.production_procurement import ProductionProcurement, ProductionQuotation
from app.services import procurement_scope as scope, production, production_quotes as quotes, production_suppliers as suppliers
from app.services.production_costing import compare_quantities, money
from app.services.spatial import _missing


_DRAFT = {"requested", "sourcing"}
_TRANSITIONS = {
    "requested": {"approved", "cancelled"}, "sourcing": {"approved", "cancelled"},
    "approved": {"ordered", "cancelled"}, "ordered": {"delivered", "cancelled"}, "delivered": set(), "cancelled": set(),
}
_SUBSTITUTION_ISSUES = {
    "specification_missing", "specification_mismatch", "finish_missing", "finish_mismatch",
    "material_substitution", "component_substitution",
}
_QUANTITY_BLOCKERS = {"quantity_shortfall", "unit_mismatch", "quoted_quantity_missing", "requirement_unmeasured"}


def procurement(db, project, row_id):
    return production._scoped_row(db, ProductionProcurement, row_id, ProductionProcurement.project_id == project.id)


def require_draft(row):
    if row.status not in _DRAFT:
        production._conflict("procurement_finalized", "Only a requested or sourcing requirement may change its selection.")


def _bom_id(row):
    return UUID(row.requirement["bom_id"])


def require_current_bom(row, boms):
    _, scene, view = boms.require(_bom_id(row))
    if not scope.bom_ready(view, approved=True) or scene.event_id != row.event_id:
        production._conflict("procurement_bom_stale", "Generate a requirement from the latest approved BOM.")
    return view


def quotation_line(row, quote):
    if (quote.bom_id != _bom_id(row) or quote.event_id != row.event_id
            or not any(line["bom_line_id"] == str(row.bom_line_id) for line in quote.lines)):
        _missing()
    return next(line for line in quote.lines if line["bom_line_id"] == str(row.bom_line_id))


def quote_line_cost(quote, line):
    if line["line_total"] is None or line["allocated_delivery"] is None:
        production._conflict("procurement_price_incomplete", "Enter a complete price and allocate delivery to the quotation line.")
    with localcontext() as context:
        context.prec = 60
        return money(Decimal(line["line_total"]) + Decimal(line["allocated_delivery"]), quote.currency_precision)


def _supplier_conditions(line, quotation):
    offering = line["offering"]
    details = offering["details"] if offering else {}
    return {"offering": offering, "minimum_quantity": details.get("minimum_quantity"), "minimum_unit": details.get("unit"),
            "lead_time_days": (quotation.details["lead_time_days"] if quotation.details["lead_time_days"] is not None
                               else details.get("lead_time_days")),
            "payment_terms": quotation.details["payment_terms"] or details.get("payment_terms"),
            "commercial_terms": details.get("commercial_terms")}


def validate_order_quantity(line, conditions, *, accept_difference):
    issues = set(line["comparison"]["issues"])
    if issues & _QUANTITY_BLOCKERS:
        production._conflict("procurement_quantity_unready", "The quotation must cover the full required quantity in a compatible unit.")
    if "quantity_difference" in issues and not accept_difference:
        production._conflict("procurement_quantity_review", "Explicitly accept the quoted surplus quantity.")
    minimum = conditions["minimum_quantity"]
    if minimum is not None:
        try:
            order = compare_quantities(Decimal(line["quantity"]), line["unit"], Decimal(minimum), conditions["minimum_unit"])
        except (ValueError, ArithmeticError):
            production._conflict("procurement_minimum_unit", "The order quantity and supplier minimum use incompatible units.")
        if order < 0:
            production._conflict("procurement_minimum_order", "The quoted quantity is below the supplier minimum order quantity.")


def _basis_view(db, project, row, boms):
    from app.services.production_value_engineering import applied_source

    reasons, issues = [], []
    source = boms.get(_bom_id(row))
    if source is None or not scope.bom_ready(source[2], approved=True):
        reasons.append("bom_changed")
    if row.selected_supplier_id is None:
        issues.append("supplier_missing")
    elif not suppliers.available_for_project(db, project, row.selected_supplier_id):
        reasons.append("supplier_unavailable")
    if row.quotation_id is None:
        issues.append("quotation_missing")
    else:
        quote = db.get(ProductionQuotation, row.quotation_id)
        if quote is None or quote.project_id != project.id:
            reasons.append("quotation_missing")
        else:
            view = quotes.quote_view(db, project, quote, boms)
            if view["stale"] or not view["latest"]:
                reasons.append("quotation_changed")
            if view["expired"]:
                reasons.append("quotation_expired")
            if quote.status != "approved":
                issues.append("quotation_not_approved")
            line = quotation_line(row, quote)
            if line["allocated_delivery"] is None or line["line_total"] is None:
                issues.append("quotation_price_incomplete")
            applied = applied_source(db, row)
            if row.selection.get("substitution") and row.status != "cancelled" and applied is None:
                reasons.append("substitution_changed")
            if _SUBSTITUTION_ISSUES.intersection(line["comparison"]["issues"]) and applied is None:
                issues.append("substitution_review_required")
    return reasons, issues


def procurement_view(db, project, row, boms):
    reasons, issues = _basis_view(db, project, row, boms)
    return {
        "id": row.id, "project_id": row.project_id, "event_id": row.event_id, "bom_line_id": row.bom_line_id,
        "selected_supplier_id": row.selected_supplier_id, "quotation_id": row.quotation_id,
        "status": row.status, "version": row.version, "requirement": row.requirement, "selection": row.selection,
        "approval": row.approval, "audit": row.audit, "created_at": row.created_at, "updated_at": row.updated_at,
        "stale": bool(reasons), "stale_reasons": reasons, "issues": issues,
    }


def generate(db, project_id, user_id, data, *, event_id=None):
    project = scope.project_scope(db, project_id, user_id, write=True)
    scope.event_scope(db, project, user_id, event_id)
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    bom, scene, view = boms.require(data.bom_id)
    scope.validate_bom_event(scene, event_id)
    if not scope.bom_ready(view, approved=True):
        production._conflict("procurement_bom_unready", "Select the latest current approved BOM.")
    candidates = {row["id"]: row for row in view["lines"] if row["basis"] == "estimated" and row["total"] is not None}
    ids = data.line_ids if data.line_ids is not None else list(candidates)
    if not ids:
        production._invalid("The BOM has no approved estimated requirements.")
    if any(line_id not in candidates for line_id in ids):
        _missing()
    existing = {row.bom_line_id: row for row in db.scalars(select(ProductionProcurement).where(
        ProductionProcurement.project_id == project.id, ProductionProcurement.bom_line_id.in_(ids),
    ))}
    rows = []
    for line_id in ids:
        row = existing.get(line_id)
        if row is None:
            line = candidates[line_id]
            row = ProductionProcurement(
                id=uuid4(), project_id=project.id, event_id=scene.event_id, bom_line_id=line_id,
                status="requested", version=1, requirement={
                    **quotes.requirement(line), "bom_id": str(bom.id), "bom_version": bom.version,
                    "currency": bom.currency, "currency_precision": bom.currency_precision,
                    "record_id": str(bom.record_id), "scene_id": str(scene.id),
                }, selection=data.model_dump(mode="json", exclude={"bom_id", "line_ids"}),
                audit=[scope.audit(user_id, "generated", bom_id=str(bom.id))],
            )
            db.add(row)
        rows.append(row)
    scope.flush(db)
    result = [procurement_view(db, project, row, boms) for row in rows]
    production._commit_changes(db)
    return result


def select_supplier(db, project_id, row_id, user_id, data):
    project = scope.project_scope(db, project_id, user_id, write=True)
    row = procurement(db, project, row_id)
    scope.event_scope(db, project, user_id, row.event_id)
    scope.check_version(data.expected_version, row.version, "procurement")
    require_draft(row)
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    require_current_bom(row, boms)
    substitution = row.selection.get("substitution")
    if substitution and (row.selected_supplier_id != data.selected_supplier_id or row.quotation_id != data.quotation_id):
        production._conflict("procurement_substitution_applied", "The applied substitution retains its approved supplier and quote.")
    ids = set(data.alternative_supplier_ids) | {data.preferred_supplier_id, data.selected_supplier_id}
    directory = {supplier_id: suppliers.supplier_for_project(db, project, supplier_id, lock=True)
                 for supplier_id in sorted(ids - {None}, key=str)}
    conditions = {}
    if data.quotation_id is not None:
        quote = quotes.quotation(db, project, data.quotation_id)
        if quote.supplier_id != data.selected_supplier_id:
            production._invalid("The quotation must come from the selected supplier.")
        line = quotation_line(row, quote)
        conditions = _supplier_conditions(line, quote)
    patch = data.model_dump(mode="json", exclude={"expected_version", "selected_supplier_id", "quotation_id"})
    for field in ("required_delivery_date", "delivery_details", "storage_details"):
        if field not in data.model_fields_set:
            patch[field] = row.selection.get(field)
    row.selection = {**patch, **conditions, "substitution": substitution,
                     "supplier_snapshots": [suppliers.supplier_snapshot(item) for item in directory.values()]}
    row.selected_supplier_id, row.quotation_id = data.selected_supplier_id, data.quotation_id
    row.status, row.version = "sourcing", row.version + 1
    row.audit = [*row.audit, scope.audit(user_id, "selection_updated", version=row.version,
                                       quotation_id=str(row.quotation_id) if row.quotation_id else None,
                                       selection=row.selection)]
    scope.flush(db)
    result = procurement_view(db, project, row, boms)
    production._commit_changes(db)
    return result


def _purchase_basis(db, project, row, boms):
    from app.services.production_value_engineering import applied_source

    require_current_bom(row, boms)
    if row.selected_supplier_id is None or row.quotation_id is None:
        production._conflict("procurement_selection_required", "Select a supplier and an approved quotation.")
    supplier = suppliers.supplier_for_project(db, project, row.selected_supplier_id, lock=True)
    quote = quotes.quotation(db, project, row.quotation_id)
    if quote.supplier_id != supplier.id:
        _missing()
    line = quotation_line(row, quote)
    quotes.require_commercial_quote(db, project, quote, boms, approved=True)
    conditions = _supplier_conditions(line, quote)
    validate_order_quantity(line, conditions, accept_difference=row.selection.get("accept_quantity_difference", False))
    substitution = row.selection.get("substitution")
    if substitution and applied_source(db, row) is None:
        production._conflict("procurement_substitution_stale", "Review and apply a current value-engineering substitution.")
    if _SUBSTITUTION_ISSUES.intersection(line["comparison"]["issues"]) and not substitution:
        production._conflict("procurement_substitution_required", "Review and apply a value-engineering substitution before approval.")
    return {
        "supplier": suppliers.supplier_snapshot(supplier), "quotation_id": str(quote.id),
        "quotation_version": quote.version, "approved_cost": str(quote_line_cost(quote, line)),
        "quantity": line["quantity"], "unit": line["unit"], "currency": quote.currency,
        "conditions": conditions, "quoted_line": line, "substitution": substitution,
    }


def transition(db, project_id, row_id, user_id, data):
    project = scope.project_scope(db, project_id, user_id, write=True)
    row = procurement(db, project, row_id)
    scope.event_scope(db, project, user_id, row.event_id)
    scope.check_version(data.expected_version, row.version, "procurement")
    if data.status not in _TRANSITIONS[row.status]:
        production._conflict("procurement_transition_invalid", "The requested procurement transition is unavailable.")
    if data.status in ("approved", "ordered") or row.status in ("approved", "ordered"):
        scope.require_approver(db, user_id, project)
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    if data.status == "approved":
        basis = _purchase_basis(db, project, row, boms)
        row.approval = {**basis, **scope.audit(user_id, "approved", note=data.note)}
    if data.recorded_date is not None and data.recorded_date > scope.today():
        production._invalid("The recorded order or delivery date cannot be in the future.")
    if data.status == "delivered":
        order_date = next(event["recorded_date"] for event in row.audit if event["action"] == "ordered")
        if data.recorded_date.isoformat() < order_date:
            production._invalid("Delivery cannot precede the recorded order.")
    row.audit = [*row.audit, scope.audit(user_id, data.status, previous_status=row.status, note=data.note,
                                       reference=data.reference, recorded_date=data.recorded_date.isoformat()
                                       if data.recorded_date else None)]
    row.status, row.version = data.status, row.version + 1
    scope.flush(db)
    result = procurement_view(db, project, row, boms)
    production._commit_changes(db)
    production._log("procurement_status_recorded", procurement_id=str(row_id), status=data.status, version=result["version"])
    return result


def get_procurement(db, project_id, row_id, user_id):
    project = scope.project_scope(db, project_id, user_id)
    row = procurement(db, project, row_id)
    scope.event_scope(db, project, user_id, row.event_id)
    return procurement_view(db, project, row, scope.BOMSources(db, project, user_id))


def list_procurements(db, project_id, user_id, *, event_id, status, offset, limit):
    project = scope.project_scope(db, project_id, user_id)
    scope.event_scope(db, project, user_id, event_id)
    query = scope.scoped_query(ProductionProcurement, project, event_id)
    if status is not None:
        query = query.where(ProductionProcurement.status == status)
    boms = scope.BOMSources(db, project, user_id)
    rows = db.scalars(query.order_by(ProductionProcurement.created_at, ProductionProcurement.id).offset(offset).limit(limit))
    return [procurement_view(db, project, row, boms) for row in rows]
