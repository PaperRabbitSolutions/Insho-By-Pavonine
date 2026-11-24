"""Supplier quotation revisions and comparable BOM line costs."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from app.models.asset import Asset
from app.models.production_procurement import ProductionQuotation, ProductionSupplierOffering
from app.services import procurement_scope as scope, production, production_suppliers as suppliers
from app.services.asset import AssetService
from app.services.production_costing import calculate_cost, compare_quantities, convert_quantity, money
from app.services.spatial import _missing


def _same_series(left, right):
    return (left.project_id == right.project_id, left.scope_key == right.scope_key,
            left.supplier_id == right.supplier_id, left.reference == right.reference)


def _latest(db, row):
    return db.scalar(select(ProductionQuotation.id).where(
        *_same_series(ProductionQuotation, row), ProductionQuotation.version > row.version,
    ).limit(1)) is None


def _attachment_available(db, project, event_id, asset_id):
    if asset_id is None:
        return False
    asset = db.get(Asset, asset_id)
    return bool(asset is not None and asset.deleted_at is None and asset.status == "active"
                and asset.organization_id == project.organization_id and asset.project_id == project.id
                and (asset.workspace_id is None or asset.workspace_id == project.workspace_id)
                and (asset.event_id is None or asset.event_id == event_id))


def quotation(db, project, quote_id):
    return production._scoped_row(db, ProductionQuotation, quote_id, ProductionQuotation.project_id == project.id)


def stale_reasons(db, project, row, boms):
    reasons = []
    source = boms.get(row.bom_id)
    if source is None or not scope.bom_ready(source[2]):
        reasons.append("bom_changed")
    elif row.event_id != source[1].event_id:
        reasons.append("bom_scope_changed")
    elif source[2]["currency"] != row.currency or source[2]["currency_precision"] != row.currency_precision:
        reasons.append("bom_currency_changed")
    if not suppliers.available_for_project(db, project, row.supplier_id):
        reasons.append("supplier_unavailable")
    if row.details["attachment"] is not None and not _attachment_available(db, project, row.event_id, row.attachment_id):
        reasons.append("attachment_unavailable")
    for line in row.lines:
        offering = line["offering"]
        if offering is not None:
            current = db.get(ProductionSupplierOffering, UUID(offering["id"]))
            if current is None or not current.active or current.version != offering["version"]:
                reasons.append("offering_changed")
                break
    for line in row.lines:
        if not line["material"] and not line["definition"]:
            continue
        try:
            material = (suppliers.material_reference(db, project.organization_id, UUID(line["material"]["id"]))
                        if line["material"] else None)
            definition = (suppliers.component_reference(db, project.organization_id, UUID(line["definition"]["id"]),
                                                       project=project) if line["definition"] else None)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            reasons.append("quoted_catalogue_changed")
            break
        if material != line["material"] or definition != line["definition"]:
            reasons.append("quoted_catalogue_changed")
            break
    return reasons


def quote_view(db, project, row, boms, *, latest=None):
    reasons = stale_reasons(db, project, row, boms)
    return {
        "id": row.id, "project_id": row.project_id, "event_id": row.event_id, "bom_id": row.bom_id,
        "supplier_id": row.supplier_id, "attachment_id": row.attachment_id, "reference": row.reference,
        "version": row.version, "status": row.status, "latest": _latest(db, row) if latest is None else latest,
        "expired": row.valid_until is not None and row.valid_until < scope.today(), "stale": bool(reasons),
        "stale_reasons": reasons, "quoted_on": row.quoted_on, "valid_until": row.valid_until,
        "currency": row.currency, "currency_precision": row.currency_precision,
        "details": row.details, "lines": row.lines, "audit": row.audit,
        "created_at": row.created_at, "created_by_user_id": row.created_by_user_id,
    }


def _normal(value):
    return " ".join(value.casefold().split()) if value else None


def _text_comparison(code, required, offered, issues):
    if required and not offered:
        issues.append(f"{code}_missing")
    elif required and _normal(required) != _normal(offered):
        issues.append(f"{code}_mismatch")


def requirement(bom_line):
    snapshot = bom_line["inputs"].get("dependencies") or {}
    material = snapshot.get("material") or {}
    sheet = snapshot.get("specification") or {}
    technical = sheet.get("technical") or {}
    quantity = snapshot.get("quantity") or {}
    component = snapshot.get("component") or {}
    return {
        "bom_line_id": str(bom_line["id"]), "line_key": bom_line["line_key"], "name": bom_line["name"],
        "category": bom_line["category"],
        "quantity": str(bom_line["quantity"]) if bom_line["quantity"] is not None else None, "unit": bom_line["unit"],
        "estimated_cost": str(bom_line["total"]) if bom_line["total"] is not None else None,
        "specification": material.get("specification") or sheet.get("content"),
        "finish": technical.get("finish") or material.get("finish"),
        "material_id": material.get("definition_id"),
        "definition_id": quantity.get("source", {}).get("library_definition_id") or component.get("source", {}).get("library_definition_id"),
        "source": snapshot,
    }


def _compare(required, line, material, definition, total, precision):
    if required is None:
        return {"comparable": False, "issues": ["unmapped_line"], "price_difference": None,
                "quantity_in_required_unit": None}
    issues, converted = [], None
    _text_comparison("specification", required["specification"], line.specification, issues)
    _text_comparison("finish", required["finish"], line.finish, issues)
    if material and material["id"] != required["material_id"]:
        issues.append("material_substitution")
    if definition and definition["id"] != required["definition_id"]:
        issues.append("component_substitution")
    if required["quantity"] is None or required["unit"] is None:
        issues.append("requirement_unmeasured")
    elif line.quantity is None or line.unit is None:
        issues.append("quoted_quantity_missing")
    else:
        try:
            converted = convert_quantity(line.quantity, line.unit, required["unit"]).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP,
            )
            order = compare_quantities(line.quantity, line.unit, Decimal(required["quantity"]), required["unit"])
            if order < 0:
                issues.append("quantity_shortfall")
            elif order > 0:
                issues.append("quantity_difference")
        except (ValueError, ArithmeticError):
            issues.append("unit_mismatch")
    if total is None:
        issues.append("quoted_cost_missing")
    if required["estimated_cost"] is None:
        issues.append("estimate_missing")
    difference = None if issues else str(money(total - Decimal(required["estimated_cost"]), precision))
    return {"comparable": not issues, "issues": issues, "price_difference": difference,
            "quantity_in_required_unit": str(converted) if converted is not None else None}


def _prepare_line(db, project, supplier, line, required, precision):
    explicit = line.model_dump(mode="json")
    offering = None
    if line.offering_id is not None:
        offering = suppliers.offering_for_supplier(db, supplier.id, line.offering_id, lock=True)
        if not offering.active:
            production._conflict("offering_unavailable", "Use an active supplier offering.")
    material_id = line.material_id or (offering.material_id if offering is not None else None)
    definition_id = line.definition_id or (offering.definition_id if offering is not None else None)
    if offering is not None and (
        (offering.material_id is not None and material_id != offering.material_id)
        or (offering.definition_id is not None and definition_id != offering.definition_id)
    ):
        production._invalid("The quoted item must match the selected offering.")
    if offering is not None and offering.details.get("unit") is not None and line.unit is not None:
        try:
            convert_quantity(Decimal(1), line.unit, offering.details["unit"])
        except ValueError:
            production._invalid("The quoted unit is incompatible with the supplier offering.")
    material = None if material_id is None else suppliers.material_reference(db, project.organization_id, material_id, lock=True)
    definition = None if definition_id is None else suppliers.component_reference(
        db, project.organization_id, definition_id, project=project, lock=True,
    )
    if material is not None and material["status"] != "active" or definition is not None and definition["status"] != "active":
        production._invalid("A quoted catalogue alternative must be active.")
    issues = [f"{field}_missing" for field in ("quantity", "unit", "unit_rate") if getattr(line, field) is None]
    total, cost = None, {}
    if not issues:
        try:
            cost = calculate_cost(line.quantity, line.unit_rate, line.discount_percent, line.tax_percent, precision)
            total = Decimal(cost["total"])
        except ValueError:
            issues.append("cost_out_of_range")
    if line.supplier_total is not None and total is not None and money(line.supplier_total, precision) != total:
        issues.append("supplier_line_total_mismatch")
    return {
        **explicit, "required": required, "material": material, "definition": definition,
        "offering": suppliers.offering_snapshot(offering) if offering is not None else None,
        "cost": cost, "line_total": str(total) if total is not None else None, "issues": issues,
        "comparison": _compare(required, line, material, definition, total, precision),
    }


def _price_lines(db, project, supplier, data, view):
    requirements = {row["id"]: requirement(row) for row in view["lines"] if row["basis"] == "estimated"}
    result = []
    precision = view["currency_precision"]
    with localcontext() as context:
        context.prec = 60
        for line in data.lines:
            if line.bom_line_id is not None and line.bom_line_id not in requirements:
                _missing()
            result.append(_prepare_line(db, project, supplier, line, requirements.get(line.bom_line_id), precision))
        header_issues = []
        if data.valid_until is None:
            header_issues.append("validity_missing")
        if data.delivery_charge is None:
            header_issues.append("delivery_charge_missing")
        if any(line["issues"] for line in result):
            header_issues.append("line_information_incomplete")
        subtotal = None if any(line["line_total"] is None for line in result) else money(
            sum((Decimal(line["line_total"]) for line in result), Decimal(0)), precision,
        )
        delivery = money(data.delivery_charge, precision) if data.delivery_charge is not None else None
        total = money(subtotal + delivery, precision) if subtotal is not None and delivery is not None else None
        if data.supplier_total is not None and total is not None and money(data.supplier_total, precision) != total:
            header_issues.append("supplier_total_mismatch")

        supplied = [None if line.delivery_amount is None else money(line.delivery_amount, precision) for line in data.lines]
        if delivery is not None:
            if len(supplied) == 1 and supplied[0] is None:
                supplied = [delivery]
            elif delivery == 0 and all(amount in (None, Decimal(0)) for amount in supplied):
                supplied = [delivery] * len(supplied)
        allocated = (delivery is not None and all(amount is not None for amount in supplied) and sum(supplied) == delivery)
        if not allocated and any(amount is not None for amount in supplied):
            header_issues.append("delivery_allocation_mismatch")
        for line, amount in zip(result, supplied):
            line["allocated_delivery"] = str(amount) if allocated else None
        total_details = {
            "subtotal": str(subtotal) if subtotal is not None else None,
            "delivery_charge": str(delivery) if delivery is not None else None,
            "total": str(total) if total is not None else None,
            "supplier_total": str(data.supplier_total) if data.supplier_total is not None else None,
            "delivery_allocated": allocated,
        }
    missing = [str(key) for key in requirements if all(line.bom_line_id != key for line in data.lines)]
    return result, total_details, header_issues, missing


def save_quote(db, project_id, user_id, data, *, event_id=None):
    project = scope.project_scope(db, project_id, user_id, write=True)
    scope.event_scope(db, project, user_id, event_id)
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    bom, scene, view = boms.require(data.bom_id)
    scope.validate_bom_event(scene, event_id)
    if not scope.bom_ready(view):
        production._conflict("quotation_bom_stale", "Select a current BOM revision for the quotation.")
    currency = data.currency.upper()
    if currency != bom.currency:
        production._invalid("Quotation currency must match the BOM currency.")
    supplier = suppliers.supplier_for_project(db, project, data.supplier_id, lock=True)
    previous = db.scalar(select(ProductionQuotation).where(
        ProductionQuotation.project_id == project.id, ProductionQuotation.scope_key == scope.scope_key(scene.event_id),
        ProductionQuotation.supplier_id == supplier.id, ProductionQuotation.reference == data.reference,
    ).order_by(ProductionQuotation.version.desc()).limit(1))
    scope.check_version(data.expected_version, previous.version if previous else 0, "quotation")
    if previous is not None:
        prior_bom = boms.get(previous.bom_id)
        if prior_bom is None or prior_bom[0].record_id != bom.record_id:
            production._invalid("A quotation revision must use the same production record.")
    attachment = None
    if data.attachment_id is not None:
        asset = AssetService.get(db, data.attachment_id, user_id, read_lock=True)
        if not _attachment_available(db, project, scene.event_id, asset.id):
            _missing()
        if asset.category not in ("document", "image"):
            production._invalid("Select a document or scanned image for the quotation.")
        attachment = {"id": str(asset.id), "filename": asset.original_filename, "sha256": asset.checksum_sha256}
    lines, totals, issues, missing = _price_lines(db, project, supplier, data, view)
    row = ProductionQuotation(
        id=uuid4(), project_id=project.id, event_id=scene.event_id, scope_key=scope.scope_key(scene.event_id),
        bom_id=bom.id, supplier_id=supplier.id, attachment_id=data.attachment_id, status="received",
        reference=data.reference, version=data.expected_version + 1, quoted_on=data.quoted_on, valid_until=data.valid_until,
        currency=currency, currency_precision=bom.currency_precision, lines=lines, created_by_user_id=user_id,
        details={
            **totals, "basis": "supplier_submitted", "issues": issues, "missing_bom_line_ids": missing,
            "supplier": suppliers.supplier_snapshot(supplier), "attachment": attachment, "bom_version": bom.version,
            "lead_time_days": data.lead_time_days, "payment_terms": data.payment_terms,
            "missing_information": [field for field in ("lead_time_days", "payment_terms", "attachment_id")
                                    if getattr(data, field) is None],
        },
        audit=[scope.audit(user_id, "recorded", previous_id=str(previous.id) if previous else None)],
    )
    db.add(row)
    scope.flush(db)
    result = quote_view(db, project, row, boms, latest=True)
    production._commit_changes(db)
    production._log("quotation_revision_saved", quotation_id=str(result["id"]), version=result["version"],
                    issue_count=len(issues))
    return result


def require_commercial_quote(db, project, row, boms, *, approved=False):
    view = quote_view(db, project, row, boms)
    if (view["stale"] or not view["latest"] or view["expired"] or row.quoted_on > scope.today()
            or (approved and row.status != "approved") or row.details["issues"] or row.details["total"] is None):
        production._conflict("quotation_unready", "Select a current, complete and valid quotation.",
                             stale_reasons=view["stale_reasons"], issues=row.details["issues"], expired=view["expired"])
    return view


def decide_quote(db, project_id, quote_id, user_id, data):
    project = scope.project_scope(db, project_id, user_id, write=True)
    scope.require_approver(db, user_id, project)
    row = quotation(db, project, quote_id)
    scope.event_scope(db, project, user_id, row.event_id)
    scope.check_version(data.expected_version, row.version, "quotation")
    if row.status != "received" or not _latest(db, row):
        production._conflict("quotation_finalized", "Only the latest received quotation can be reviewed.")
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    if data.decision == "approved":
        suppliers.supplier_for_project(db, project, row.supplier_id, lock=True)
        require_commercial_quote(db, project, row, boms)
    row.status = data.decision
    row.audit = [*row.audit, scope.audit(user_id, row.status, note=data.note)]
    result = quote_view(db, project, row, boms, latest=True)
    production._commit_changes(db)
    production._log("quotation_decided", quotation_id=str(quote_id), decision=data.decision)
    return result


def get_quote(db, project_id, quote_id, user_id):
    project = scope.project_scope(db, project_id, user_id)
    row = quotation(db, project, quote_id)
    scope.event_scope(db, project, user_id, row.event_id)
    return quote_view(db, project, row, scope.BOMSources(db, project, user_id))


def _current_filter(row):
    newer = aliased(ProductionQuotation)
    return ~exists(select(newer.id).where(*_same_series(newer, row), newer.version > row.version))


def list_quotes(db, project_id, user_id, *, event_id, history, offset, limit, status=None):
    project = scope.project_scope(db, project_id, user_id)
    scope.event_scope(db, project, user_id, event_id)
    query = scope.scoped_query(ProductionQuotation, project, event_id)
    if not history:
        query = query.where(_current_filter(ProductionQuotation))
    if status is not None:
        query = query.where(ProductionQuotation.status == status)
    rows = db.scalars(query.order_by(ProductionQuotation.created_at.desc(), ProductionQuotation.id).offset(offset).limit(limit))
    boms = scope.BOMSources(db, project, user_id)
    return [quote_view(db, project, row, boms, latest=None if history else True) for row in rows]


def compare_quotes(db, project_id, bom_id, user_id, *, offset, limit):
    project = scope.project_scope(db, project_id, user_id)
    boms = scope.BOMSources(db, project, user_id)
    bom, _, _ = boms.require(bom_id)
    rows = db.scalars(select(ProductionQuotation).where(
        ProductionQuotation.project_id == project.id, ProductionQuotation.bom_id == bom.id,
        _current_filter(ProductionQuotation),
    ).order_by(ProductionQuotation.created_at, ProductionQuotation.id).offset(offset).limit(limit))
    return {"bom_id": bom.id, "currency": bom.currency, "currency_precision": bom.currency_precision,
            "quotes": [quote_view(db, project, row, boms, latest=True) for row in rows]}
