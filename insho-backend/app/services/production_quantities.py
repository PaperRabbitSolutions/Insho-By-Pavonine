"""Append-only quantity calculations scoped to existing production records."""

from typing import Optional
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.models.event import Event
from app.models.production import ProductionItem, ProductionQuantity, ProductionRecord
from app.models.spatial import SpatialScene
from app.schemas.production_quantities import QuantityRule
from app.security import require_project_access
from app.services import production
from app.services.production_geometry import ProductionGeometry, fingerprint, target_id
from app.services.production_quantity_rules import COUNT_METHODS, ENGINE_VERSION, evaluate, parameters, variant
from app.services.spatial import _missing, _validate_event_project


_rule_adapter = TypeAdapter(QuantityRule)


def _same_line(left, right):
    return (left.record_id == right.record_id, left.target == right.target, left.target_id == right.target_id,
            left.method == right.method, left.variant == right.variant)


def _is_latest(row):
    newer = aliased(ProductionQuantity)
    return ~exists(select(newer.id).where(*_same_line(newer, row), newer.version > row.version))


def _current(db, record_id):
    return db.scalars(select(ProductionQuantity).where(ProductionQuantity.record_id == record_id,
                                                      _is_latest(ProductionQuantity))).all()


def _signature(geometry, target, source_id, method):
    inputs, footprint, world = geometry.read(target, source_id, count=method in COUNT_METHODS)
    return fingerprint(inputs), inputs, footprint, world


def _stale(row, geometry):
    if row.engine_version != ENGINE_VERSION or geometry.scene.deleted_at is not None:
        return True
    signature, _, _, _ = _signature(geometry, row.target, row.target_id, row.method)
    return row.source_fingerprint != signature


def _view(row, record, geometry, latest=True):
    scene = geometry.scene
    approved = latest and record.status == "approved"
    return {
        "id": row.id, "record_id": record.id, "item_id": row.item_id,
        "scene_id": scene.id, "project_id": scene.project_id, "event_id": scene.event_id,
        "method": row.method, "version": row.version, "engine_version": row.engine_version,
        "scene_revision": row.scene_revision, "current_scene_revision": scene.current_revision,
        "source": row.source, "formula": row.formula, "parameters": row.parameters, "inputs": row.inputs,
        "outputs": row.outputs, "value": row.value, "unit": row.unit, "status": row.status, "errors": row.errors,
        "stale": _stale(row, geometry), "latest": latest, "approved": approved,
        "approved_at": record.approved_at if approved else None,
        "approved_by_user_id": record.approved_by_user_id if approved else None,
        "calculated_by_user_id": row.calculated_by_user_id, "calculated_at": row.calculated_at,
    }


def _prepare(record, geometry, item, target, source_id, rule, user_id, latest):
    if latest is not None and latest.item_id != item.id:
        production._conflict("quantity_source_already_used", "This source and rule already belong to another production item.")
    config = parameters(rule)
    signature, inputs, footprint, world = _signature(geometry, target, source_id, rule.method)
    if (latest is not None and latest.engine_version == ENGINE_VERSION
            and latest.source_fingerprint == signature and latest.parameters == config):
        return latest, False
    formula, unit, value, outputs, errors = evaluate(rule, target, inputs, footprint, world)
    origin = geometry.provenance(item, target, source_id)
    if latest is not None and "design" in latest.source and "design" not in origin:
        origin["design"] = latest.source["design"]
    row = ProductionQuantity(
        id=uuid4(), record_id=record.id, item_id=item.id, target=target, target_id=source_id, method=rule.method,
        variant=variant(rule), version=1 if latest is None else latest.version + 1, engine_version=ENGINE_VERSION,
        scene_revision=geometry.scene.current_revision, source_fingerprint=signature,
        source=origin, parameters=config, inputs=inputs,
        outputs=outputs, formula=formula, unit=unit, value=value, errors=errors,
        status="invalid" if errors else "calculated", calculated_by_user_id=user_id,
    )
    return row, True


def _scope_draft(db, scene_id, record_id, user_id, data):
    scene = production._scene(db, scene_id, user_id, write=True)
    record = production._draft(db, scene, record_id, data.expected_version)
    production._check_scene_revision(scene, data.scene_revision)
    return record, ProductionGeometry(db, scene)


def _commit(db, record, geometry, prepared):
    new = [row for row, changed in prepared if changed]
    if new:
        db.add_all(new)
        record.version += 1
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            production._conflict("production_relationship_conflict", "The change conflicts with a production relationship.")
    result = {"record_version": record.version, "quantities": [_view(row, record, geometry) for row, _ in prepared]}
    if new:
        production._commit_changes(db)
        quantities = result["quantities"]
        production._log("quantity_calculation_saved", record_id=str(quantities[0]["record_id"]),
                        calculated_count=len(new), invalid_count=sum(row["status"] == "invalid" for row in quantities),
                        version=result["record_version"])
    return result


def _key(target, source_id, method, rule_variant):
    return target, source_id, method, rule_variant


def calculate(db, scene_id, record_id, user_id, data):
    record, geometry = _scope_draft(db, scene_id, record_id, user_id, data)
    items = {item.id: item for item in db.scalars(select(ProductionItem).where(
        ProductionItem.record_id == record.id, ProductionItem.id.in_({row.item_id for row in data.calculations}),
    ))}
    previous = {_key(row.target, row.target_id, row.method, row.variant): row for row in _current(db, record.id)}
    seen = set()
    prepared = []
    for entry in data.calculations:
        item = items.get(entry.item_id)
        if item is None:
            _missing()
        source_id = target_id(item, geometry.scene, entry.target)
        original_id = item.source.get({"object": "spatial_object_id", "zone": "spatial_zone_id"}.get(entry.target))
        if source_id is None and original_id:
            source_id = UUID(original_id)
        if source_id is None:
            production._invalid("The production item has no reference for this calculation target.")
        key = _key(entry.target, source_id, entry.rule.method, variant(entry.rule))
        if key in seen:
            production._conflict("duplicate_quantity_source", "Each source and rule may appear once in the batch.")
        seen.add(key)
        prepared.append(_prepare(record, geometry, item, entry.target, source_id, entry.rule, user_id, previous.get(key)))
    return _commit(db, record, geometry, prepared)


def recalculate(db, scene_id, record_id, quantity_id, user_id, data):
    record, geometry = _scope_draft(db, scene_id, record_id, user_id, data)
    previous = _get(db, record, quantity_id)
    if not previous[1]:
        production._conflict("quantity_version_superseded", "Reload the latest quantity version before recalculating.")
    row = previous[0]
    item = production._item(db, record, row.item_id)
    rule = _rule_adapter.validate_python({"method": row.method, **row.parameters})
    result = _commit(db, record, geometry, [
        _prepare(record, geometry, item, row.target, row.target_id, rule, user_id, row),
    ])
    production._log("quantity_recalculated", previous_quantity_id=str(quantity_id),
                    quantity_id=str(result["quantities"][0]["id"]))
    return result


def _get(db, record, quantity_id):
    row = db.execute(select(ProductionQuantity, _is_latest(ProductionQuantity).label("latest")).where(
        ProductionQuantity.record_id == record.id, ProductionQuantity.id == quantity_id,
    )).one_or_none()
    if row is None:
        _missing()
    return row


def detail(db, scene_id, record_id, quantity_id, user_id):
    scene = production._scene(db, scene_id, user_id)
    record = production._record(db, scene, record_id)
    row, latest = _get(db, record, quantity_id)
    return _view(row, record, ProductionGeometry(db, scene), latest)


def list_for_record(db, scene_id, record_id, user_id, *, history, offset, limit):
    scene = production._scene(db, scene_id, user_id)
    record = production._record(db, scene, record_id)
    latest = _is_latest(ProductionQuantity)
    query = select(ProductionQuantity, latest.label("latest")).where(ProductionQuantity.record_id == record.id)
    if not history:
        query = query.where(latest)
    rows = db.execute(query.order_by(ProductionQuantity.calculated_at.desc(), ProductionQuantity.id)
                      .offset(offset).limit(limit)).all()
    geometry = ProductionGeometry(db, scene)
    return [_view(row, record, geometry, is_latest) for row, is_latest in rows]


def list_for_project(db, project_id, user_id, *, event_id: Optional[UUID], history, offset, limit):
    project = require_project_access(db, user_id, project_id)
    _validate_event_project(db, user_id, project, event_id)
    latest = _is_latest(ProductionQuantity)
    query = (select(ProductionQuantity, ProductionRecord, SpatialScene, latest.label("latest"))
             .join(ProductionRecord, ProductionRecord.id == ProductionQuantity.record_id)
             .join(SpatialScene, SpatialScene.id == ProductionRecord.scene_id)
             .outerjoin(Event, Event.id == SpatialScene.event_id)
             .where(SpatialScene.project_id == project.id, or_(
                 SpatialScene.event_id.is_(None), (Event.project_id == project.id) & (Event.organization_id == project.organization_id),
             )))
    if event_id is not None:
        query = query.where(SpatialScene.event_id == event_id)
    if not history:
        query = query.where(latest)
    rows = db.execute(query.order_by(ProductionQuantity.calculated_at.desc(), ProductionQuantity.id)
                      .offset(offset).limit(limit).with_for_update(read=True, of=SpatialScene)).all()
    geometry_by_scene = {}
    for _, _, scene, _ in rows:
        if scene.id not in geometry_by_scene:
            geometry_by_scene[scene.id] = ProductionGeometry(db, scene)
    return [_view(row, record, geometry_by_scene[scene.id], is_latest) for row, record, scene, is_latest in rows]


def approval_blockers(db, record, geometry):
    return [str(row.id) for row in _current(db, record.id) if row.status != "calculated" or _stale(row, geometry)]
