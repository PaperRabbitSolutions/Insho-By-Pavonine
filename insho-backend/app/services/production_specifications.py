"""Technical sheet source capture, freshness and approval for existing specifications."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.models.production import ProductionItem, ProductionMaterial, ProductionQuantity, ProductionSpecification
from app.models.project import Project
from app.models.spatial import SpatialScene
from app.services import production, production_quantities
from app.services.production_geometry import ProductionGeometry, fingerprint, target_id
from app.services.production_units import normalize


def _source_material(db, item):
    row = db.get(ProductionMaterial, item.material_id) if item.material_id is not None else None
    if row is None:
        return None
    snapshot = {"id": str(row.id), "name": row.name, "description": row.description}
    if row.definition_id is not None:
        from app.services.production_materials import MaterialResolver

        scene = db.get(SpatialScene, item.scene_id)
        project = db.get(Project, scene.project_id)
        effective = MaterialResolver(db, project.organization_id).effective(row)
        if effective is not None:
            snapshot.update({field: effective[field] for field in ("definition_id", "unit", "specification", "finish")})
    return snapshot


def _inputs(geometry, target, source_id):
    if target is None:
        return {"state": "user_entered"}
    inputs, _, _ = geometry.read(target, source_id)
    node = geometry.node(target, source_id)
    if node is not None:
        # Installation depends on placement, even when a translation leaves quantities unchanged.
        world = node["world_matrix"]
        inputs["position_m"] = None if world is None else [row[3] for row in world[:3]]
        parent = node["parent"]
        inputs["parent"] = None if parent is None else {"type": parent["type"], "id": str(parent["id"])}
        inputs.update(appearance=node["appearance"], material_refs=node["material_refs"])
    return inputs


def _dimension(kind, value, unit):
    return {"value": str(normalize(kind, Decimal(str(value)), unit)), "unit": "m"}


def _quantities(db, record, item, ids):
    items = {row.id: row for row in db.scalars(select(ProductionItem).where(ProductionItem.record_id == record.id))}
    allowed = {item.id, *production.item_ancestors(item, items)}
    return {row.id: row for row in db.scalars(select(ProductionQuantity).where(
        ProductionQuantity.record_id == record.id, ProductionQuantity.id.in_(ids), ProductionQuantity.item_id.in_(allowed),
    ))}


def _unverified(db, record, geometry, rows):
    current = {row.id for row in production_quantities._current(db, record.id)}
    return any(row.id not in current or row.status != "calculated" or production_quantities._stale(row, geometry)
               for row in rows)


def capture(db, scene, record, item, data, user_id):
    production._check_scene_revision(scene, data.scene_revision)
    target = data.target
    if target is None:
        if "spatial_object_id" in item.source:
            target = "object"
        elif "spatial_zone_id" in item.source:
            target = "zone"
    source_id = target_id(item, scene, target) if target is not None else None
    geometry = ProductionGeometry(db, scene)
    inputs = _inputs(geometry, target, source_id)
    if inputs["state"] in {"missing", "archived"}:
        production._invalid("The technical specification requires a live source target.")
    try:
        if data.source == "spatial_geometry":
            dimensions = geometry.dimensions(target, source_id)
            if not dimensions:
                raise ValueError("The source has no supported dimensions.")
            measured = {kind: _dimension(kind, value, "m") for kind, value in dimensions.items()}
        else:
            measured = {kind: _dimension(kind, amount.value, amount.unit) for kind, amount in data.dimensions.items()}
    except (ValueError, ArithmeticError) as exc:
        production._invalid(str(exc))

    origin = {"target": None, "target_id": None}
    if target is not None:
        origin = geometry.provenance(item, target, source_id)
    quantity_refs = []
    if data.quantity_ids:
        quantities = _quantities(db, record, item, data.quantity_ids)
        if len(quantities) != len(data.quantity_ids):
            production._missing()
        if _unverified(db, record, geometry, quantities.values()):
            production._conflict("specification_quantity_unverified", "Select current, valid quantity revisions.")
        quantity_refs = [{"id": str(key), "version": quantities[key].version} for key in data.quantity_ids]
    return {
        "source": {"type": data.source, "scene_revision": scene.current_revision, "captured_by_user_id": str(user_id), **origin},
        "source_fingerprint": fingerprint(inputs), "dimensions": measured,
        "quantity_refs": quantity_refs,
        "material": _source_material(db, item), "finish": data.finish,
        "fabrication_notes": data.fabrication_notes, "installation_requirements": data.installation_requirements,
        "parameters": [parameter.model_dump(mode="json") for parameter in data.parameters],
        "parameter_source": "user_entered",
    }


def _stale_reasons(db, record, geometry, item, snapshot):
    if snapshot is None:
        return []
    scene = geometry.scene
    if scene.deleted_at is not None:
        return ["scene_deleted"]
    source = snapshot["source"]
    target = source["target"]
    source_id = UUID(source["target_id"]) if source["target_id"] is not None else None
    reasons = []
    if target is not None and source_id != target_id(item, scene, target):
        reasons.append("source_missing")
    elif fingerprint(_inputs(geometry, target, source_id)) != snapshot["source_fingerprint"]:
        reasons.append("spatial_source_changed")
    if _source_material(db, item) != snapshot["material"]:
        reasons.append("material_changed")
    refs = snapshot.get("quantity_refs", [])
    if refs:
        quantities = _quantities(db, record, item, [UUID(ref["id"]) for ref in refs])
        if len(quantities) != len(refs) or _unverified(db, record, geometry, quantities.values()):
            reasons.append("quantity_changed")
    return reasons


def views(db, record, geometry, item, rows):
    superseded = {row.supersedes_id: row.id for row in rows if row.supersedes_id is not None}
    results = []
    for row in rows:
        reasons = _stale_reasons(db, record, geometry, item, row.technical)
        approved_at, approved_by = row.approved_at, row.approved_by_user_id
        if approved_at is None and record.status == "approved" and row.id not in superseded:
            approved_at, approved_by = record.approved_at, record.approved_by_user_id
        status = "approved" if approved_at is not None else "draft"
        if status == "draft" and row.technical is not None and row.technical["source"]["type"] == "spatial_geometry":
            status = "suggestion"
        technical = None if row.technical is None else {
            key: value for key, value in row.technical.items() if key != "source_fingerprint"
        }
        results.append({
            "id": row.id, "name": row.name, "content": row.content, "version": row.version,
            "technical": technical, "status": status, "stale": bool(reasons), "stale_reasons": reasons,
            "supersedes_id": row.supersedes_id, "superseded_by_id": superseded.get(row.id),
            "approved_at": approved_at, "approved_by_user_id": approved_by,
        })
    return results


def _read(db, scene_id, record_id, item_id, user_id):
    scene = production._scene(db, scene_id, user_id)
    record = production._record(db, scene, record_id)
    item = production._item(db, record, item_id)
    rows = db.scalars(select(ProductionSpecification).where(ProductionSpecification.item_id == item.id)
                      .order_by(ProductionSpecification.name, ProductionSpecification.id)).all()
    return views(db, record, ProductionGeometry(db, scene), item, rows)


def list_for_item(db, scene_id, record_id, item_id, user_id):
    return _read(db, scene_id, record_id, item_id, user_id)


def detail(db, scene_id, record_id, item_id, specification_id, user_id):
    row = next((row for row in _read(db, scene_id, record_id, item_id, user_id) if row["id"] == specification_id), None)
    if row is None:
        production._missing()
    return row


def approve(db, scene_id, record_id, item_id, specification_id, user_id, data):
    scene = production._scene(db, scene_id, user_id, write=True)
    production._require_approver(db, scene, user_id)
    record = production._draft(db, scene, record_id, data.expected_version)
    production._check_scene_revision(scene, data.scene_revision)
    item = production._item(db, record, item_id)
    row = production._scoped_row(db, ProductionSpecification, specification_id, ProductionSpecification.item_id == item.id)
    if row.approved_at is not None:
        production._conflict("specification_approved", "The specification has already been approved.")
    reasons = _stale_reasons(db, record, ProductionGeometry(db, scene), item, row.technical)
    if reasons:
        production._conflict("specification_sources_stale", "Review the technical specification sources.", reasons=reasons)
    row.approved_at, row.approved_by_user_id = datetime.now(timezone.utc), user_id
    result = production._save(db, record, row.id)
    production._log("technical_specification_approved", specification_id=str(specification_id))
    return result


def require_no_approved_specs(db, *, item_id=None, record_id=None):
    statement = select(ProductionSpecification.id).where(ProductionSpecification.approved_at.is_not(None))
    if item_id is not None:
        statement = statement.where(ProductionSpecification.item_id == item_id)
    else:
        statement = statement.join(ProductionItem, ProductionItem.id == ProductionSpecification.item_id)
        statement = statement.where(ProductionItem.record_id == record_id)
    if db.scalar(statement.limit(1)) is not None:
        production._conflict("approved_specification_history", "This production data retains an approved specification.")
