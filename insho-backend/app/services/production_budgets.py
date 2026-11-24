"""Versioned project and event allocations over Part 3 and supplier cost sources."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.production_procurement import ProductionBudget, ProductionProcurement, ProductionQuotation
from app.services import procurement_scope as scope, production, production_quotes as quotes
from app.services.production_costing import money
from app.services.production_procurement import _QUANTITY_BLOCKERS, _SUBSTITUTION_ISSUES
from app.services.production_value_engineering import applied_source


_BASES = ("estimated", "quoted", "actual")


def _head(db, project, event_id):
    return db.scalar(select(ProductionBudget).where(
        ProductionBudget.project_id == project.id, ProductionBudget.scope_key == scope.scope_key(event_id),
    ).order_by(ProductionBudget.version.desc()).limit(1))


def _budget(db, project, row_id):
    return production._scoped_row(db, ProductionBudget, row_id, ProductionBudget.project_id == project.id)


def _recorded_amount(amount, precision):
    return money(amount, precision) if amount is not None else None


def _quote_amount(line, precision):
    if line["line_total"] is None or line["allocated_delivery"] is None or line["issues"]:
        return None
    return money(Decimal(line["line_total"]) + Decimal(line["allocated_delivery"]), precision)


def _quote_requirements(db, project, quotation):
    reviewed = {}
    for line in quotation.lines:
        line_id = line["bom_line_id"]
        issues = set(line["comparison"]["issues"])
        if line_id is not None and issues & (_QUANTITY_BLOCKERS | _SUBSTITUTION_ISSUES):
            reviewed[line_id] = {"eligible": False, "substitution": None}
            if issues & _QUANTITY_BLOCKERS:
                continue
            request = db.scalar(select(ProductionProcurement).where(
                ProductionProcurement.project_id == project.id, ProductionProcurement.quotation_id == quotation.id,
                ProductionProcurement.bom_line_id == UUID(line_id),
            ))
            applied = applied_source(db, request) if request is not None else None
            if applied is not None:
                reviewed[line_id] = {"eligible": True, "substitution": applied}
    return reviewed


def _collect(db, project, event_id, data, boms, currency):
    precision = data.currency_precision
    budgets_by_category = defaultdict(lambda: defaultdict(list))
    snapshots, scene_ids = {}, set()
    for bom_id in data.bom_ids:
        bom, scene, view = boms.require(bom_id)
        scope.validate_bom_event(scene, event_id)
        if scene.id in scene_ids:
            production._invalid("Select one production plan per scene.")
        scene_ids.add(scene.id)
        if not scope.bom_ready(view):
            production._conflict("budget_bom_stale", "Use current BOM revisions in a budget.")
        if (bom.currency, bom.currency_precision) != (currency, precision):
            production._invalid("Budget currency and precision must match each BOM.")
        snapshots[bom_id] = (bom, scene, view)

    quote_lines = defaultdict(dict)
    quote_sources = []
    for quote_id in data.quote_ids:
        row = quotes.quotation(db, project, quote_id)
        if row.bom_id not in snapshots:
            production._invalid("Select the BOM linked to each quotation in the budget.")
        quotes.require_commercial_quote(db, project, row, boms, approved=True)
        reviews = _quote_requirements(db, project, row)
        for line in row.lines:
            line_id = line["bom_line_id"]
            if line_id is None:
                budgets_by_category["other"]["quoted"].append(_quote_amount(line, precision))
            elif line_id in quote_lines[row.bom_id]:
                production._conflict("budget_duplicate_quote_coverage", "Select one quoted price per BOM requirement.")
            else:
                coverage = reviews.get(line_id)
                quote_lines[row.bom_id][line_id] = (_quote_amount(line, precision)
                                                    if coverage is None or coverage["eligible"] else None)
        # A quote source replaces the manually recorded Part 3 quote basis for its BOM.
        quote_lines[row.bom_id]
        quote_sources.append({"id": str(row.id), "version": row.version, "bom_id": str(row.bom_id),
                              "reference": row.reference, "supplier_id": str(row.supplier_id),
                              "quoted_amount": row.details["total"], "requirement_reviews": reviews})
    bom_sources = []
    for bom_id, (bom, scene, view) in snapshots.items():
        coverage = quote_lines.get(bom_id)
        bom_sources.append({"id": str(bom_id), "version": bom.version, "record_id": str(bom.record_id),
                            "scene_id": str(scene.id), "event_id": str(scene.event_id) if scene.event_id else None})
        for line in view["lines"]:
            by_basis = budgets_by_category[line["category"]]
            if coverage is None or line["basis"] != "quoted":
                by_basis[line["basis"]].append(line["total"] if not line["stale"] else None)
            if coverage is not None and line["basis"] == "estimated":
                by_basis["quoted"].append(coverage.get(str(line["id"])))
    return budgets_by_category, bom_sources, quote_sources


def _basis(values, precision):
    subtotal = money(sum((value for value in values if value is not None), Decimal(0)), precision)
    missing = sum(value is None for value in values)
    return {"total": str(subtotal) if values and not missing else None,
            "priced_subtotal": str(subtotal) if any(value is not None for value in values) else None,
            "recorded_lines": len(values) - missing, "unpriced_lines": missing}


def _cost_snapshot(db, project, event_id, data, boms, currency):
    precision = data.currency_precision
    with localcontext() as context:
        context.prec = 60
        breakdown, bom_sources, quote_sources = _collect(db, project, event_id, data, boms, currency)
        categories = {
            category: {basis: _basis(breakdown[category][basis], precision) for basis in _BASES}
            for category in sorted(set(breakdown) | set(data.allocations))
        }
    return categories, bom_sources, quote_sources


def _reasons(db, project, event, row, boms):
    reasons = []
    if event is not None and event.currency is not None and event.currency.upper() != row.currency:
        reasons.append("event_currency_changed")
    legacy = row.sources.get("legacy")
    if legacy is not None:
        source = event if event is not None else project
        if source.budget is None or source.budget != Decimal(legacy["amount"]):
            reasons.append("planning_amount_changed")
    for item in row.sources["boms"]:
        current = boms.get(UUID(item["id"]))
        if current is None or not scope.bom_ready(current[2]):
            reasons.append("bom_changed")
            break
    for item in row.sources["quotes"]:
        quote = db.scalar(select(ProductionQuotation).where(
            ProductionQuotation.id == UUID(item["id"]), ProductionQuotation.project_id == project.id,
        ))
        if quote is None:
            reasons.append("quotation_changed")
            break
        view = quotes.quote_view(db, project, quote, boms)
        if view["expired"] or view["stale"] or not view["latest"] or quote.status != "approved":
            reasons.append("quotation_changed")
            break
        if item.get("requirement_reviews", {}) != _quote_requirements(db, project, quote):
            reasons.append("quotation_requirements_changed")
            break
    return reasons


def _variance(amount, totals, precision):
    actual = totals["actual"]["total"]
    if amount is None or actual is None:
        actual_variance = remaining = None
    else:
        actual_variance, remaining = str(money(Decimal(actual) - amount, precision)), str(money(amount - Decimal(actual), precision))
    return {
        "remaining": remaining, "actual_variance": actual_variance,
        "estimated_variance": str(money(Decimal(totals["estimated"]["total"]) - amount, precision))
        if amount is not None and totals["estimated"]["total"] is not None else None,
        "quoted_variance": str(money(Decimal(totals["quoted"]["total"]) - amount, precision))
        if amount is not None and totals["quoted"]["total"] is not None else None,
    }


def _view(db, project, event, row, boms, *, latest):
    precision = row.currency_precision
    approved = row.approved_at is not None
    with localcontext() as context:
        context.prec = 60
        amount = money(row.amount, precision)
        allocations = {category: Decimal(value) for category, value in row.allocations.items()}
        categories = {}
        for category, totals in row.costs.items():
            allocation = allocations.get(category)
            categories[category] = {
                "allocation": str(allocation) if allocation is not None else None,
                "approved_amount": str(allocation) if allocation is not None and approved else None, **totals,
                **_variance(allocation if approved else None, totals, precision),
            }
        summary = {}
        for basis in _BASES:
            contents = [totals[basis] for totals in row.costs.values()]
            priced = [Decimal(cost["priced_subtotal"]) for cost in contents if cost["priced_subtotal"] is not None]
            complete = bool(contents) and all(cost["total"] is not None for cost in contents)
            summary[basis] = {
                "total": str(money(sum(priced), precision)) if complete else None,
                "priced_subtotal": str(money(sum(priced), precision)) if priced else None,
                "unpriced_lines": sum(cost["unpriced_lines"] for cost in contents),
                "unrecorded_categories": sum(cost["recorded_lines"] == 0 and cost["unpriced_lines"] == 0 for cost in contents),
            }
        summary.update({"approved_amount": str(amount) if approved else None,
                        **_variance(amount if approved else None, summary, precision)})
        unallocated = money(amount - sum(allocations.values()), precision)
    reasons = _reasons(db, project, event, row, boms)
    return {
        "id": row.id, "project_id": row.project_id, "event_id": row.event_id, "version": row.version, "latest": latest,
        "currency": row.currency, "currency_precision": precision, "amount": amount, "unallocated": unallocated,
        "allocations": allocations, "sources": row.sources, "categories": categories, "summary": summary,
        "approved_at": row.approved_at, "approved_by_user_id": row.approved_by_user_id, "created_at": row.created_at,
        "created_by_user_id": row.created_by_user_id, "stale": bool(reasons), "stale_reasons": reasons,
    }


def save_budget(db, project_id, user_id, data, *, event_id=None):
    project = scope.project_scope(db, project_id, user_id, write=True)
    event = scope.event_scope(db, project, user_id, event_id)
    head = _head(db, project, event_id)
    scope.check_version(data.expected_version, head.version if head else 0, "budget")
    currency = data.currency.upper()
    if event is not None and event.currency and event.currency.upper() != currency:
        production._invalid("Budget currency must match the linked event currency.")
    legacy = None
    amount = data.amount
    if amount is None:
        planning = event if event is not None else project
        if planning.budget is None:
            production._invalid("Supply a budget amount or configure the existing planning amount.")
        amount = planning.budget
        legacy = {"kind": "event" if event is not None else "project", "id": str(planning.id),
                  "version": planning.version, "amount": str(amount)}
    with localcontext() as context:
        context.prec = 60
        precision = data.currency_precision
        amount = _recorded_amount(amount, precision)
        allocations = {key: _recorded_amount(value, precision) for key, value in data.allocations.items()}
        if sum(allocations.values()) > amount:
            production._invalid("Allocated amounts cannot exceed the budget amount.")
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    costs, bom_sources, quote_sources = _cost_snapshot(db, project, event_id, data, boms, currency)
    row = ProductionBudget(
        id=uuid4(), project_id=project.id, event_id=event_id, scope_key=scope.scope_key(event_id),
        version=data.expected_version + 1, currency=currency, currency_precision=precision, amount=amount,
        allocations={key: str(value) for key, value in allocations.items()}, costs=costs,
        sources={"legacy": legacy, "boms": bom_sources, "quotes": quote_sources, "note": data.note},
        created_by_user_id=user_id,
    )
    db.add(row)
    scope.flush(db)
    result = _view(db, project, event, row, boms, latest=True)
    production._commit_changes(db)
    production._log("budget_revision_saved", budget_id=str(result["id"]), version=result["version"])
    return result


def approve_budget(db, project_id, row_id, user_id, data):
    project = scope.project_scope(db, project_id, user_id, write=True)
    scope.require_approver(db, user_id, project)
    row = _budget(db, project, row_id)
    event = scope.event_scope(db, project, user_id, row.event_id)
    scope.check_version(data.expected_version, row.version, "budget")
    if _head(db, project, row.event_id).id != row.id:
        production._conflict("budget_superseded", "Approve the latest budget revision.")
    boms = scope.BOMSources(db, project, user_id, lock_rates=True)
    newly_approved = row.approved_at is None
    if newly_approved:
        reasons = _reasons(db, project, event, row, boms)
        if reasons:
            production._conflict("budget_sources_stale", "Review the budget sources in a new revision.", reasons=reasons)
        row.approved_at, row.approved_by_user_id = datetime.now(timezone.utc), user_id
        row.sources = {**row.sources, "approval_note": data.note}
    result = _view(db, project, event, row, boms, latest=True)
    production._commit_changes(db)
    if newly_approved:
        production._log("budget_approved", budget_id=str(row_id), version=result["version"])
    return result


def get_budget(db, project_id, row_id, user_id):
    project = scope.project_scope(db, project_id, user_id)
    row = _budget(db, project, row_id)
    event = scope.event_scope(db, project, user_id, row.event_id)
    return _view(db, project, event, row, scope.BOMSources(db, project, user_id), latest=_head(db, project, row.event_id).id == row.id)


def list_budgets(db, project_id, user_id, *, event_id, offset, limit):
    project = scope.project_scope(db, project_id, user_id)
    event = scope.event_scope(db, project, user_id, event_id)
    query = select(ProductionBudget).where(ProductionBudget.project_id == project.id,
                                           ProductionBudget.scope_key == scope.scope_key(event_id))
    rows = db.scalars(query.order_by(ProductionBudget.version.desc()).offset(offset).limit(limit))
    head = _head(db, project, event_id)
    boms = scope.BOMSources(db, project, user_id)
    return [_view(db, project, event, row, boms, latest=row.id == head.id) for row in rows]
