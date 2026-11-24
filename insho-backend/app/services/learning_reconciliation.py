"""Decimal comparisons against frozen design and production estimates."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.models.learning import LearningActual
from app.schemas.learning import Comparison, Source
from app.services import design_snapshot
from app.services.learning_actuals import active_actuals
from app.services.learning_sources import distinct, invalid, reference
from app.services.production_costing import convert_quantity


METHOD_VERSION = "section29-v1"
MAX_INPUTS = 500


def _selections(ctx, selectors):
    results = {}
    for item in selectors:
        if item.metric == "cost":
            basis = ctx.cost_basis(item.cost)
        elif item.metric == "quantity":
            basis = ctx.quantity_basis(item.bom_line_id)
        else:
            basis = ctx.installation_basis(item.activity)
        results[basis["key"]] = basis
    return results


def _compare(basis, rows):
    sources = [Source.model_validate(basis["source"])]
    confirmed = [row for row in rows if row.status == "verified"]
    complete = [row for row in confirmed if row.data["coverage"] == "complete"]
    sources.extend(reference("actual", row.id, row.revision) for row in rows)
    for row in confirmed:
        sources.extend(Source.model_validate(item) for item in row.sources if item["kind"] != "asset")
    row = complete[0] if len(complete) == 1 else confirmed[0] if not complete and len(confirmed) == 1 else None
    if row is not None and basis["metric"] in {"labour", "wastage"}:
        basis = row.derived["comparison"]
    estimate, expected_unit = basis["estimate"], basis["estimate_unit"]
    if estimate is not None:
        estimate = Decimal(estimate)
    actual = Decimal(row.derived["actual"]) if row is not None else None
    actual_unit = row.derived["actual_unit"] if row is not None else None
    result = {"metric": basis["metric"], "key": basis["key"], "category": basis["category"],
              "comparison_group": basis["comparison_group"], "estimate": estimate, "actual": actual,
              "estimate_unit": expected_unit, "actual_unit": actual_unit, "actual_ids": [item.id for item in rows],
              "sources": distinct(sources), "verified_actual_id": row.id if row is not None else None,
              "actual_in_estimate_units": None, "difference": None,
              "absolute_difference": None, "percentage_difference": None, "conversion_method": None}
    if len(complete) > 1 or (not complete and len(confirmed) > 1):
        status = "ambiguous_actual"
    elif confirmed and not complete:
        status = "partial_actual"
    elif not rows:
        status = "missing_actual"
    elif row is None:
        status = "unverified_actual"
    elif estimate is None:
        status = "missing_estimate"
    elif basis["metric"] == "cost" and expected_unit != actual_unit:
        status = "currency_mismatch"
    else:
        status = "compared"
        try:
            with localcontext() as arithmetic:
                arithmetic.prec = 96
                if actual_unit == expected_unit:
                    normalized = actual
                    method = "same_unit"
                else:
                    normalized = convert_quantity(actual, actual_unit, expected_unit)
                    normalized = normalized.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                    method = "phase10_production_unit_conversion"
                change = normalized - estimate
                percentage = (100 * change / estimate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if estimate else None
                result.update(actual_in_estimate_units=normalized, difference=change, absolute_difference=abs(change),
                              percentage_difference=percentage, conversion_method=method)
        except ValueError:
            status = "incompatible_unit"
    return Comparison(status=status, **result)


def build(db, ctx, payload):
    query = active_actuals(ctx.project, [ctx.event.id]).where(
        LearningActual.design_version_id == ctx.version.id,
        LearningActual.kind.in_(("cost", "quantity", "labour", "installation", "wastage")),
    )
    rows = db.scalars(query.order_by(LearningActual.id).limit(MAX_INPUTS + 1)).all()
    if len(rows) > MAX_INPUTS:
        invalid("This version exceeds the reconciliation input limit.")
    groups = defaultdict(list)
    bases = _selections(ctx, payload.estimates)
    selected = set(bases)
    for row in rows:
        basis = row.derived["comparison"]
        groups[basis["key"]].append(row)
        bases.setdefault(basis["key"], basis)
    # A draft contains an authored plan, which becomes an eligible estimate only after verification.
    for key, group in groups.items():
        if key not in selected and bases[key]["metric"] in {"labour", "wastage"}:
            bases[key] = {**bases[key], "estimate": None}
    comparisons = [_compare(bases[key], groups[key]) for key in sorted(bases)]
    evidence = distinct([reference("design_version", ctx.version.id), *(source for item in comparisons for source in item.sources)])
    content = {"comparisons": [item.model_dump(mode="json") for item in comparisons],
               "evidence": [item.model_dump(mode="json") for item in evidence]}
    fingerprint = design_snapshot.digest({"method": METHOD_VERSION, "version": str(ctx.version.id), "content": content})
    return content, fingerprint
