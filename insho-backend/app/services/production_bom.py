"""Build revisioned BOMs from current takeoffs, technical sheets, and explicit inputs."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.models.event import Event
from app.models.production import ProductionItem, ProductionMaterial, ProductionQuantity, ProductionRecord, ProductionSpecification
from app.models.production_bom import ProductionBOM, ProductionBOMLine
from app.models.spatial import SpatialScene
from app.security import require_project_access
from app.services import production, production_quantities, production_specifications
from app.services.production_costing import FORMULA, calculate_cost, convert_quantity, decimal_text, money, required_quantity
from app.services.production_geometry import ProductionGeometry, fingerprint
from app.services.production_materials import MaterialResolver
from app.services.spatial import _missing, _validate_event_project


_FIELDS = ("name", "origin", "basis", "category", "quantity", "unit", "unit_rate", "total", "inputs", "cost", "issues")
_BASIS = ("estimated", "quoted", "actual")


def _issue(code, message):
    return {"code": code, "message": message}


def _quantity_key(row):
    return row.item_id, row.target, row.target_id, row.method, row.variant


class Sources:
    def __init__(self, db, scene, record, organization_id, *, lock_rates=False):
        self.db, self.scene, self.record = db, scene, record
        self.geometry = ProductionGeometry(db, scene)
        self.materials = MaterialResolver(db, organization_id, lock=lock_rates)
        self.items = {row.id: row for row in db.scalars(select(ProductionItem).where(ProductionItem.record_id == record.id))}
        self.usages = {row.id: row for row in db.scalars(select(ProductionMaterial).where(
            ProductionMaterial.record_id == record.id,
        ))}
        self.quantities = {_quantity_key(row): row for row in production_quantities._current(db, record.id)}
        self.specs = defaultdict(list)
        if self.items:
            for row in db.scalars(select(ProductionSpecification).where(ProductionSpecification.item_id.in_(self.items))):
                self.specs[row.item_id].append(row)
        self.spec_views = {}

    def quantity(self, quantity_id):
        return production._scoped_row(self.db, ProductionQuantity, quantity_id, ProductionQuantity.record_id == self.record.id)

    def sheets(self, item):
        if item.id not in self.spec_views:
            self.spec_views[item.id] = production_specifications.views(
                self.db, self.record, self.geometry, item, self.specs[item.id],
            )
        return self.spec_views[item.id]

    def dependencies(self, item, quantity, spec_id):
        parents = production.item_ancestors(item, self.items)
        component = {"id": str(item.id), "name": item.name, "kind": item.kind,
                     "parent_ids": [str(row) for row in parents], "source": item.source}
        material = self.materials.effective(self.usages.get(item.material_id))
        sheets = self.sheets(item)
        if spec_id is None:
            eligible = [row for row in sheets if row["approved_at"] and row["superseded_by_id"] is None
                        and row["technical"] and not row["stale"]]
            sheet = eligible[0] if len(eligible) == 1 else None
        else:
            sheet = next((row for row in sheets if row["id"] == spec_id), None)
        technical = None if sheet is None else {
            "id": str(sheet["id"]), "version": sheet["version"], "name": sheet["name"], "content": sheet["content"],
            "technical": sheet["technical"], "status": sheet["status"], "stale": sheet["stale"],
            "superseded": sheet["superseded_by_id"] is not None,
            "approved_at": sheet["approved_at"].astimezone(timezone.utc).isoformat() if sheet["approved_at"] else None,
            "approved_by_user_id": str(sheet["approved_by_user_id"]) if sheet["approved_by_user_id"] else None,
        }
        latest = self.quantities.get(_quantity_key(quantity))
        takeoff = {
            "id": str(quantity.id), "item_id": str(quantity.item_id), "version": quantity.version,
            "scene_revision": quantity.scene_revision, "engine_version": quantity.engine_version, "method": quantity.method,
            "unit": quantity.unit, "value": str(quantity.value) if quantity.value is not None else None,
            "status": quantity.status, "source": quantity.source, "parameters": quantity.parameters, "formula": quantity.formula,
            "latest": latest is not None and latest.id == quantity.id,
            "stale": production_quantities._stale(quantity, self.geometry),
        }
        return {"component": component, "quantity": takeoff, "specification": technical, "material": material}


def _origins(sources, line):
    if sources.scene.deleted_at is not None:
        return ["scene_deleted"]
    dependencies = line.inputs.get("dependencies")
    item_id = line.inputs.get("item_id")
    if dependencies is None:
        return ["component_missing"] if item_id is not None and UUID(item_id) not in sources.items else []
    item = sources.items.get(UUID(dependencies["component"]["id"]))
    if item is None:
        return ["component_missing"]
    takeoff = dependencies["quantity"]
    quantity = sources.db.get(ProductionQuantity, UUID(takeoff["id"]))
    if quantity is None:
        return ["quantity_missing"]
    spec_id = line.inputs.get("requested_specification_id")
    current = sources.dependencies(item, quantity, UUID(spec_id) if spec_id else None)
    reasons = []
    if current["component"] != dependencies["component"]:
        reasons.append("component_changed")
    if current["quantity"] != takeoff or current["quantity"]["stale"] or not current["quantity"]["latest"]:
        reasons.append("quantity_changed")
    specification = current["specification"]
    if (specification != dependencies["specification"]
            or (specification is not None and (specification["stale"] or specification["superseded"]))):
        reasons.append("specification_changed")
    if current["material"] != dependencies["material"]:
        reasons.append("material_changed")
    return reasons


def _copy(row):
    return {**{field: getattr(row, field) for field in _FIELDS}, "entered_by_user_id": row.entered_by_user_id}


def _price(quantity, rate, data, precision, issues):
    if rate is None:
        issues.append(_issue("missing_rate", "Supply an explicit unit rate in the BOM currency."))
    if issues:
        return {"formula": FORMULA}, None
    try:
        cost = calculate_cost(quantity, rate, data.discount_percent, data.tax_percent, precision)
    except ValueError as exc:
        issues.append(_issue("invalid_cost", str(exc)))
        return {"formula": FORMULA}, None
    return cost, Decimal(cost["total"])


def _adjustments(data):
    return {"discount_percent": decimal_text(data.discount_percent), "tax_percent": decimal_text(data.tax_percent)}


def _material_quantity(quantity, material, consumption):
    with localcontext() as context:
        context.prec = 60
        if consumption is None:
            amount = convert_quantity(quantity.value, quantity.unit, material["unit"])
        else:
            source = convert_quantity(quantity.value, quantity.unit, consumption.per_unit)
            result = source / consumption.per_quantity * consumption.quantity
            amount = convert_quantity(result, consumption.unit, material["unit"])
        return required_quantity(amount, material["unit"], Decimal(material["allowance_percent"]))


def _generated(sources, data, user_id, currency, precision):
    quantity = sources.quantity(data.quantity_id)
    item = sources.items.get(data.item_id or quantity.item_id)
    if item is None:
        _missing()
    if quantity.item_id != item.id and quantity.item_id not in production.item_ancestors(item, sources.items):
        production._invalid("A takeoff must belong to the component or one of its parent assemblies.")
    if data.specification_id is not None and all(row.id != data.specification_id for row in sources.specs[item.id]):
        _missing()
    dependencies = sources.dependencies(item, quantity, data.specification_id)
    material, technical, takeoff = (dependencies[key] for key in ("material", "specification", "quantity"))
    issues = []
    if takeoff["status"] != "calculated" or takeoff["stale"] or not takeoff["latest"]:
        issues.append(_issue("quantity_unverified", "Select the latest valid and current Part 2 calculation."))
    if technical is None:
        issues.append(_issue("missing_specification", "Select a current approved structured technical specification."))
    elif technical["status"] != "approved" or technical["stale"] or technical["superseded"] or not technical["technical"]:
        issues.append(_issue("specification_unverified", "The technical specification requires current approval."))
    elif technical["technical"].get("quantity_refs") and not any(
        ref["id"] == str(quantity.id) for ref in technical["technical"]["quantity_refs"]
    ):
        issues.append(_issue("specification_quantity_mismatch", "Select a takeoff referenced by the technical specification."))
    if material is None:
        issues.append(_issue("missing_material_mapping", "Link the item material assignment to a catalogue definition."))
    elif material["status"] != "active":
        issues.append(_issue("material_unavailable", "The specified material is unavailable or archived."))
    if material is not None and technical is not None and technical["technical"]:
        captured = technical["technical"].get("material") or {}
        if captured.get("id") != material["id"] or captured.get("definition_id") != material["definition_id"]:
            issues.append(_issue("specification_material_mismatch", "Review the technical sheet for this material assignment."))
    amount = None
    if material is not None and not issues:
        try:
            amount = _material_quantity(quantity, material, data.consumption)
        except (ValueError, ArithmeticError) as exc:
            issues.append(_issue("incompatible_quantity", str(exc)))
    rate = None if material is None or material["unit_rate"] is None else Decimal(material["unit_rate"])
    if material is not None and material.get("override_rate_unit") is not None:
        issues.append(_issue("override_rate_unit_mismatch", "Review the project rate after the material unit changed."))
    if rate is not None and material["currency"] != currency:
        issues.append(_issue("currency_mismatch", "The material rate uses a different currency."))
    cost, total = _price(amount, rate, data, precision, issues)
    consumption = None
    if data.consumption is not None:
        consumption = data.consumption.model_dump(mode="json")
        consumption.update(quantity=decimal_text(data.consumption.quantity),
                           per_quantity=decimal_text(data.consumption.per_quantity))
    inputs = {
        "item_id": str(item.id), "dependencies": dependencies, "currency": currency,
        "requested_specification_id": str(data.specification_id) if data.specification_id else None,
        "consumption": consumption, **_adjustments(data),
    }
    key = fingerprint([str(item.id), str(quantity.item_id), quantity.target, str(quantity.target_id), quantity.method, quantity.variant])
    return key, {"name": item.name, "origin": "generated", "basis": "estimated", "category": "material",
                 "quantity": amount, "unit": material["unit"] if material else None, "unit_rate": rate, "total": total,
                 "inputs": inputs, "cost": cost, "issues": issues, "entered_by_user_id": user_id}


def _manual(sources, data, prior, user_id, currency, precision):
    origin = prior.inputs if prior is not None else {}
    item_id = str(data.item_id) if data.item_id is not None else origin.get("item_id")
    if item_id is not None and UUID(item_id) not in sources.items:
        _missing()
    if origin.get("dependencies") is not None and item_id != origin["item_id"]:
        production._invalid("Keep the source item when revising a generated line.")
    # Preserve generated source dependencies for an explicit adjustment to a takeoff.
    inputs = {**origin, "item_id": item_id, "source_reference": data.source_reference,
              "currency": currency, **_adjustments(data)}
    issues = []
    cost, total = _price(data.quantity, data.unit_rate, data, precision, issues)
    return {"name": data.name.strip(), "origin": "manual", "basis": data.basis, "category": data.category,
            "quantity": data.quantity, "unit": data.unit, "unit_rate": data.unit_rate, "total": total,
            "inputs": inputs, "cost": cost, "issues": issues, "entered_by_user_id": user_id}


def _header(bom, record, scene, *, latest):
    return {
        "id": bom.id, "record_id": record.id, "scene_id": scene.id, "event_id": scene.event_id, "project_id": scene.project_id,
        "version": bom.version, "record_version": bom.record_version, "current_record_version": record.version,
        "scene_revision": bom.scene_revision, "current_scene_revision": scene.current_revision,
        "currency": bom.currency, "currency_precision": bom.currency_precision,
        "note": bom.note, "latest": latest, "created_at": bom.created_at, "created_by_user_id": bom.created_by_user_id,
        "approved_at": bom.approved_at, "approved_by_user_id": bom.approved_by_user_id,
    }


def _summarize(lines, precision):
    costs = []
    with localcontext() as context:
        context.prec = 60
        for basis in _BASIS:
            scoped = [line for line in lines if line["basis"] == basis]
            if not scoped:
                continue
            subtotal = money(sum((line["total"] for line in scoped if line["total"] is not None), Decimal(0)), precision)
            unpriced = sum(line["total"] is None for line in scoped)
            stale = sum(line["stale"] for line in scoped)
            costs.append({"basis": basis, "priced_subtotal": subtotal, "total": subtotal if not unpriced and not stale else None,
                          "unpriced_lines": unpriced, "stale_lines": stale})
    return costs


def _view(bom, lines, sources, *, latest):
    event = sources.db.get(Event, sources.scene.event_id) if sources.scene.event_id is not None else None
    currency_changed = event is not None and event.currency is not None and event.currency.upper() != bom.currency
    views = []
    for line in lines:
        reasons = _origins(sources, line)
        if currency_changed:
            reasons.append("event_currency_changed")
        row = {**_copy(line), "id": line.id, "line_key": line.line_key, "stale_reasons": reasons, "stale": bool(reasons)}
        rate_currency = None
        if line.unit_rate is not None:
            if line.origin == "manual":
                rate_currency = bom.currency
            else:
                rate_currency = line.inputs["dependencies"]["material"]["currency"]
        row["unit_rate_currency"] = rate_currency
        if row["total"] is not None:
            row["total"] = money(row["total"], bom.currency_precision)
        views.append(row)
    return {**_header(bom, sources.record, sources.scene, latest=latest), "lines": views,
            "stale": any(row["stale"] for row in views), "costs": _summarize(views, bom.currency_precision)}


def _head(db, record):
    return db.scalar(select(ProductionBOM).where(ProductionBOM.record_id == record.id)
                     .order_by(ProductionBOM.version.desc()).limit(1).execution_options(populate_existing=True))


def _lines(db, bom_id):
    return db.scalars(select(ProductionBOMLine).where(ProductionBOMLine.bom_id == bom_id)
                      .order_by(ProductionBOMLine.position, ProductionBOMLine.id)).all()


def _scope(db, scene_id, record_id, user_id, *, write=False):
    scene = production._scene(db, scene_id, user_id, write=write)
    record = production._record(db, scene, record_id, write=write)
    project = require_project_access(db, user_id, scene.project_id)
    return scene, record, project


def _check_versions(scene, record, head, data):
    production._check_scene_revision(scene, data.scene_revision)
    if record.version != data.expected_record_version:
        production._conflict("production_version_conflict", "Reload the production record.", current_version=record.version)
    current = head.version if head else 0
    if current != data.expected_bom_version:
        production._conflict("bom_version_conflict", "Reload the current BOM.", current_version=current)


def _save(db, bom, lines, sources, *, insert=False):
    if insert:
        db.add(bom)
    try:
        db.flush()
        if insert:
            db.add_all(lines)
            db.flush()
    except IntegrityError:
        db.rollback()
        production._conflict("bom_relationship_conflict", "The BOM revision conflicts with an existing record.")
    result = _view(bom, lines, sources, latest=True)
    production._commit_changes(db)
    production._log("bom_revision_saved" if insert else "bom_approved", bom_id=str(result["id"]),
                    version=result["version"], unpriced_count=sum(line["total"] is None for line in result["lines"]))
    return result


def revise(db, scene_id, record_id, user_id, data):
    scene, record, project = _scope(db, scene_id, record_id, user_id, write=True)
    head = _head(db, record)
    _check_versions(scene, record, head, data)
    currency = data.currency.upper()
    event = db.get(Event, scene.event_id) if scene.event_id is not None else None
    if event is not None and event.currency is not None and event.currency.upper() != currency:
        production._invalid("The BOM currency must match the linked event currency.")
    if head is not None and (head.currency, head.currency_precision) != (currency, data.currency_precision):
        production._invalid("Revisions use the currency and precision of the original BOM.")
    sources = Sources(db, scene, record, project.organization_id, lock_rates=True)
    previous = _lines(db, head.id) if head else []
    by_id = {row.id: row for row in previous}
    by_key = {row.line_key: row for row in previous}
    draft = {row.line_key: _copy(row) for row in previous}
    seen = set()
    for change in data.changes:
        if change.type == "generate":
            key, values = _generated(sources, change, user_id, currency, data.currency_precision)
            prior = by_key.get(key)
            if prior is not None and prior.origin == "manual" and not change.replace_manual:
                production._conflict("bom_manual_line", "Explicitly confirm replacement of the manually adjusted line.",
                                     line_id=str(prior.id))
        else:
            prior = by_id.get(change.line_id)
            if change.line_id is not None and prior is None:
                _missing()
            key = prior.line_key if prior is not None else uuid4().hex
            values = None if change.type == "remove" else _manual(sources, change, prior, user_id, currency, data.currency_precision)
        if key in seen:
            production._conflict("duplicate_bom_line", "Each logical BOM line may appear once per revision request.")
        seen.add(key)
        if values is None:
            del draft[key]
        elif prior is None or any(values[name] != getattr(prior, name) for name in _FIELDS):
            draft[key] = values
    if not draft:
        production._invalid("A BOM must contain at least one line.")
    if head is not None and len(draft) == len(previous) and all(draft.get(row.line_key) == _copy(row) for row in previous):
        return _view(head, previous, sources, latest=True)
    bom = ProductionBOM(
        id=uuid4(), record_id=record.id, version=head.version + 1 if head is not None else 1,
        record_version=record.version, scene_revision=scene.current_revision, currency=currency,
        currency_precision=data.currency_precision, note=data.note, created_by_user_id=user_id,
    )
    lines = [ProductionBOMLine(id=uuid4(), bom_id=bom.id, line_key=key, position=index, **values)
             for index, (key, values) in enumerate(draft.items())]
    return _save(db, bom, lines, sources, insert=True)


def list_revisions(db, scene_id, record_id, user_id, *, offset, limit):
    scene, record, _ = _scope(db, scene_id, record_id, user_id)
    head = _head(db, record)
    rows = db.scalars(select(ProductionBOM).where(ProductionBOM.record_id == record.id)
                      .order_by(ProductionBOM.version.desc()).offset(offset).limit(limit))
    return [_header(row, record, scene, latest=row.id == head.id) for row in rows]


def get_revision(db, scene_id, record_id, bom_id, user_id):
    scene, record, project = _scope(db, scene_id, record_id, user_id)
    bom = production._scoped_row(db, ProductionBOM, bom_id, ProductionBOM.record_id == record.id)
    return _view(bom, _lines(db, bom.id), Sources(db, scene, record, project.organization_id),
                 latest=bom.id == _head(db, record).id)


def approve(db, scene_id, record_id, bom_id, user_id, data):
    scene, record, project = _scope(db, scene_id, record_id, user_id, write=True)
    production._require_approver(db, scene, user_id)
    bom = production._scoped_row(db, ProductionBOM, bom_id, ProductionBOM.record_id == record.id)
    head = _head(db, record)
    _check_versions(scene, record, head, data)
    if head.id != bom.id:
        production._conflict("bom_superseded", "Approve the latest BOM revision.")
    sources = Sources(db, scene, record, project.organization_id, lock_rates=True)
    lines = _lines(db, bom.id)
    if bom.approved_at is not None:
        return _view(bom, lines, sources, latest=True)
    view = _view(bom, lines, sources, latest=True)
    blockers = [str(line["id"]) for line in view["lines"] if line["issues"] or line["stale"]]
    if blockers:
        production._conflict("bom_unready", "Resolve missing, invalid, or stale BOM lines before approval.", line_ids=blockers)
    bom.approved_at, bom.approved_by_user_id = datetime.now(timezone.utc), user_id
    return _save(db, bom, lines, sources)


def _no_newer_bom(row, *, approved=False):
    newer = aliased(ProductionBOM)
    query = select(newer.id).where(newer.record_id == row.record_id, newer.version > row.version)
    if approved:
        query = query.where(newer.approved_at.is_not(None))
    return ~exists(query)


def _rollup(records):
    scenes = defaultdict(int)
    for record in records:
        scenes[record["scene_id"]] += 1
    ambiguous = sorted((key for key, count in scenes.items() if count > 1), key=str)
    if ambiguous:
        return [], ambiguous
    groups = defaultdict(list)
    for record in records:
        for cost in record["costs"]:
            groups[(record["currency"], record["currency_precision"], cost["basis"])].append(cost)
    totals = []
    with localcontext() as context:
        context.prec = 60
        for (currency, precision, basis), costs in sorted(groups.items()):
            subtotal = money(sum((cost["priced_subtotal"] for cost in costs), Decimal(0)), precision)
            unpriced = sum(cost["unpriced_lines"] for cost in costs)
            stale = sum(cost["stale_lines"] for cost in costs)
            totals.append({
                "currency": currency, "currency_precision": precision, "basis": basis, "priced_subtotal": subtotal,
                "total": subtotal if not unpriced and not stale else None, "unpriced_lines": unpriced, "stale_lines": stale,
            })
    return totals, ambiguous


def project_costs(db, project_id, user_id, *, event_id=None, record_ids=None):
    project = require_project_access(db, user_id, project_id)
    _validate_event_project(db, user_id, project, event_id)
    eligible = (select(ProductionRecord.id)
                .join(SpatialScene, SpatialScene.id == ProductionRecord.scene_id)
                .outerjoin(Event, Event.id == SpatialScene.event_id)
                .where(SpatialScene.project_id == project.id, or_(
                    SpatialScene.event_id.is_(None),
                    (Event.project_id == project.id) & (Event.organization_id == project.organization_id),
                )))
    if event_id is not None:
        eligible = eligible.where(SpatialScene.event_id == event_id)
    if record_ids:
        eligible = eligible.where(ProductionRecord.id.in_(record_ids))
        if set(db.scalars(eligible)) != set(record_ids):
            _missing()
    latest, latest_approved = _no_newer_bom(ProductionBOM), _no_newer_bom(ProductionBOM, approved=True)
    approved = ProductionBOM.approved_at.is_not(None) & latest_approved
    query = (select(ProductionBOM, ProductionRecord, SpatialScene, latest.label("latest"), approved.label("approved"))
             .join(ProductionRecord, ProductionRecord.id == ProductionBOM.record_id)
             .join(SpatialScene, SpatialScene.id == ProductionRecord.scene_id)
             .where(ProductionBOM.record_id.in_(eligible), or_(latest, approved))
             .order_by(SpatialScene.id, ProductionRecord.id, ProductionBOM.version.desc())
             .with_for_update(read=True, of=SpatialScene))
    current, approvals, contexts = [], [], {}
    for bom, record, scene, is_latest, is_approved in db.execute(query):
        if record.id not in contexts:
            contexts[record.id] = Sources(db, scene, record, project.organization_id)
        snapshot = _view(bom, _lines(db, bom.id), contexts[record.id], latest=is_latest)
        summary = {key: value for key, value in snapshot.items() if key != "lines"}
        if is_latest:
            current.append(summary)
        if is_approved:
            approvals.append(summary)
    totals, ambiguous = _rollup(current)
    approved_totals, approved_ambiguous = _rollup(approvals)
    return {
        "project_id": project.id, "event_id": event_id, "records": current, "approved_records": approvals,
        "totals": totals, "approved_totals": approved_totals, "ambiguous_scene_ids": ambiguous,
        "approved_ambiguous_scene_ids": approved_ambiguous,
        "unestimated_record_ids": sorted(set(record_ids or ()) - {row["record_id"] for row in current}, key=str),
    }
