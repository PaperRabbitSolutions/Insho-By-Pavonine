"""Scene-scoped production edits, source capture, and review."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.object_library import ObjectDefinition
from app.models.production import (
    ProductionItem, ProductionMaterial, ProductionMeasurement, ProductionRecord, ProductionSpecification,
    ProductionWorkflowRevision,
)
from app.models.spatial import SpatialObject, SpatialZone
from app.models.spatial_design_review import SpatialDesignReview
from app.security import ADMIN_ROLES, require_membership, require_project_access
from app.services.object_library import scene_definition
from app.services.production_units import CANONICAL_UNITS, normalize
from app.services.spatial import _get_scene, _missing
from app.services.spatial_graph import GraphRead
from app.services.spatial_policy import scene_row
from app.services.spatial_runtime import SceneCompilation
from app.spatial.geometry2d import geometry_area
from app.spatial.geometry3d import Parallelepiped3
from app.spatial.projection import project_xy
from app.spatial.types import Vec3


_SPATIAL_DIMENSIONS = {"width": "width_m", "length": "depth_m", "height": "height_m"}
logger = logging.getLogger("insho.production")


def _log(action, **fields):
    logger.info(action, extra={"production_action": action, **fields})


def _conflict(code, message, **details):
    _log("production_conflict", error_code=code)
    raise HTTPException(409, detail={"code": code, "message": message, **details})


def _invalid(message):
    raise HTTPException(422, detail=message)


def _scene(db, scene_id, user_id, *, write=False):
    scene = _get_scene(db, scene_id, user_id, write=write, lock="read", include_deleted=True)
    if write and scene.deleted_at is not None:
        _conflict("scene_deleted", "The production source scene has been deleted.")
    return scene


def _scoped_row(db, model, row_id, scope):
    row = db.scalar(select(model).where(model.id == row_id, scope))
    if row is None:
        _missing()
    return row


def _record(db, scene, record_id, *, write=False):
    statement = select(ProductionRecord).where(
        ProductionRecord.id == record_id, ProductionRecord.scene_id == scene.id,
    ).with_for_update(read=not write).execution_options(populate_existing=True)
    row = db.scalar(statement)
    if row is None:
        _missing()
    return row


def _draft(db, scene, record_id, expected_version):
    row = _record(db, scene, record_id, write=True)
    if row.version != expected_version:
        _conflict("production_version_conflict", "Reload the production record.", current_version=row.version)
    if row.status != "draft":
        _conflict("production_approved", "The production record has been approved.")
    return row


def _check_scene_revision(scene, expected):
    if scene.current_revision != expected:
        _conflict("scene_revision_conflict", "Reload the source scene.", current_revision=scene.current_revision)


def _record_view(row, scene):
    return {
        "id": row.id, "scene_id": scene.id, "project_id": scene.project_id, "event_id": scene.event_id,
        "name": row.name, "status": row.status, "version": row.version, "scene_revision": row.scene_revision,
        "supersedes_id": row.supersedes_id,
        "current_scene_revision": scene.current_revision,
        "scene_stale": row.scene_revision != scene.current_revision or scene.deleted_at is not None,
        "created_by_user_id": row.created_by_user_id, "approved_by_user_id": row.approved_by_user_id,
        "approved_at": row.approved_at, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _save(db, record, target=None, *, scene=None, advance=True):
    if advance:
        record.version += 1
    try:
        db.flush()
        result = (_record_view(record, scene) if scene is not None
                  else {"id": target if target is not None else record.id, "record_version": record.version})
        _commit_changes(db)
        return result
    except IntegrityError:
        db.rollback()
        logger.warning("production_transaction_failed", extra={"operation": "flush"})
        _conflict("production_relationship_conflict", "The change conflicts with a production relationship.")


def _commit_changes(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("production_transaction_failed", extra={"operation": "commit"})
        _conflict("production_relationship_conflict", "The change conflicts with a production relationship.")


def create_record(db, scene_id, user_id, data):
    scene = _scene(db, scene_id, user_id, write=True)
    if data.supersedes_id is not None:
        previous = _record(db, scene, data.supersedes_id)
        if previous.approved_at is None:
            _conflict("production_predecessor_unapproved", "Select an approved production record to replace.")
    row = ProductionRecord(
        scene_id=scene.id, name=data.name.strip(), scene_revision=scene.current_revision, created_by_user_id=user_id,
        supersedes_id=data.supersedes_id,
    )
    db.add(row)
    return _save(db, row, scene=scene, advance=False)


def list_records(db, scene_id, user_id, *, offset, limit):
    scene = _scene(db, scene_id, user_id)
    rows = db.scalars(select(ProductionRecord).where(ProductionRecord.scene_id == scene.id)
                      .order_by(ProductionRecord.created_at, ProductionRecord.id).offset(offset).limit(limit))
    return [_record_view(row, scene) for row in rows]


def rename_record(db, scene_id, record_id, user_id, data):
    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, data.expected_version)
    record.name = data.name.strip()
    return _save(db, record, scene=scene)


def delete_record(db, scene_id, record_id, user_id, expected_version):
    from app.models.production_bom import ProductionBOM
    from app.models.production_procurement import ProductionQuotation
    from app.services.procurement_scope import protect_budget_bom_history
    from app.services.production_specifications import require_no_approved_specs

    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, expected_version)
    if db.get(ProductionWorkflowRevision, (record.id, 1)) is not None:
        _conflict("production_workflow_history", "This production record retains workflow history.")
    require_no_approved_specs(db, record_id=record.id)
    if db.scalar(select(ProductionBOM.id).where(ProductionBOM.record_id == record.id,
                                                ProductionBOM.approved_at.is_not(None)).limit(1)):
        _conflict("approved_bom_history", "This production record retains an approved BOM.")
    if db.scalar(select(ProductionQuotation.id).join(ProductionBOM, ProductionBOM.id == ProductionQuotation.bom_id)
                 .where(ProductionBOM.record_id == record.id).limit(1)):
        _conflict("quotation_history", "This production record retains supplier quotation history.")
    protect_budget_bom_history(db, scene.project_id, (record.id,))
    db.delete(record)
    _commit_changes(db)


def _object_zone(obj, objects):
    # A nested object inherits the assignment of its nearest ancestor with a zone.
    visited = set()
    while obj is not None:
        if obj.zone_id is not None:
            return obj.zone_id
        if obj.id in visited:
            _conflict("spatial_hierarchy_cycle", "The source object hierarchy contains a cycle.")
        visited.add(obj.id)
        obj = objects(obj.parent_object_id) if obj.parent_object_id else None
    return None


def _review_scene_revision(review):
    return review.outcome["scene_revision"] if review.status == "applied" else review.base_revision


def _audit(db, record, scene, *, lock_catalog=False):
    from app.services.production_geometry import ProductionGeometry
    from app.services.production_specifications import views as specification_views

    items = db.scalars(select(ProductionItem).where(ProductionItem.record_id == record.id)
                       .order_by(ProductionItem.name, ProductionItem.id)).all()
    item_ids = select(ProductionItem.id).where(ProductionItem.record_id == record.id)
    specifications = defaultdict(list)
    spec_versions = {}
    superseded_specs = set()
    for spec in db.scalars(select(ProductionSpecification).where(ProductionSpecification.item_id.in_(item_ids))
                           .order_by(ProductionSpecification.name, ProductionSpecification.id)):
        specifications[spec.item_id].append(spec)
        spec_versions[str(spec.id)] = spec.version
        if spec.supersedes_id is not None and spec.approved_at is not None:
            superseded_specs.add(spec.supersedes_id)
    measurements = db.scalars(select(ProductionMeasurement).where(ProductionMeasurement.item_id.in_(item_ids))
                              .order_by(ProductionMeasurement.kind)).all()
    definitions = {item.library_definition_id for item in items if item.library_definition_id is not None}
    catalog = {}
    if definitions:
        statement = select(ObjectDefinition).where(ObjectDefinition.id.in_(definitions)).order_by(ObjectDefinition.id)
        if lock_catalog:
            statement = statement.with_for_update(read=True)
        catalog = {row.id: row for row in db.scalars(statement.execution_options(populate_existing=True))}
    recommendations = {row.recommendation_id for row in measurements if row.recommendation_id is not None}
    reviews = {}
    if recommendations:
        reviews = {row.recommendation_id: row for row in db.scalars(select(SpatialDesignReview).where(
            SpatialDesignReview.scene_id == scene.id, SpatialDesignReview.recommendation_id.in_(recommendations),
        ).execution_options(populate_existing=True))}
    objects = {}
    if any(item.spatial_object_id is not None for item in items):
        objects = {row.id: row for row in db.scalars(select(SpatialObject).where(SpatialObject.scene_id == scene.id))}
    measurements_by_item = defaultdict(list)
    for row in measurements:
        measurements_by_item[row.item_id].append(row)

    views = []
    spec_geometry = ProductionGeometry(db, scene)
    for item in items:
        reasons = []
        for field in ("spatial_object_id", "spatial_zone_id", "library_definition_id"):
            if field in item.source and getattr(item, field) is None:
                reasons.append(field.removesuffix("_id") + "_missing")
        obj = objects.get(item.spatial_object_id)
        if obj is not None and _object_zone(obj, objects.get) != item.spatial_zone_id:
            reasons.append("object_zone_changed")
        definition = catalog.get(item.library_definition_id)
        if definition is not None and definition.version != item.reviewed_library_version:
            reasons.append("library_definition_changed")
        item_specs = specification_views(db, record, spec_geometry, item, specifications[item.id])
        stale_specs = {spec["id"] for spec in item_specs if spec["stale"]}
        if any(spec["stale"] and spec["superseded_by_id"] is None for spec in item_specs):
            reasons.append("specification_source_changed")
        values = []
        for row in measurements_by_item[item.id]:
            source = row.source
            stale = False
            if row.source_type == "spatial_geometry":
                live_target = {"object": item.spatial_object_id, "zone": item.spatial_zone_id, "scene": scene.id}
                stale = (scene.deleted_at is not None or source.get("scene_revision") != scene.current_revision
                         or str(live_target.get(source.get("target"))) != source.get("target_id"))
            elif row.source_type == "object_library":
                stale = (definition is None or definition.status != "active"
                         or definition.version != source.get("definition_version"))
            elif row.source_type == "technical_specification":
                stale = (row.specification_id in (superseded_specs | stale_specs)
                         or str(row.specification_id) not in spec_versions
                         or spec_versions[str(row.specification_id)] != source.get("specification_version"))
            elif row.source_type == "ai_suggestion":
                review = reviews.get(row.recommendation_id)
                stale = (scene.deleted_at is not None or review is None or review.status == "rejected"
                         or str(review.review_id) != source.get("review_id")
                         or source.get("scene_revision") != scene.current_revision)
            provenance = {"type": row.source_type, **source}
            if row.specification_id is not None:
                provenance["specification_id"] = row.specification_id
            if row.recommendation_id is not None:
                provenance["recommendation_id"] = row.recommendation_id
            values.append({
                "kind": row.kind, "value": row.value, "unit": CANONICAL_UNITS[row.kind],
                "source": provenance, "stale": stale,
                "entered_by_user_id": row.entered_by_user_id,
            })
        if any(value["stale"] for value in values):
            reasons.append("measurement_source_changed")
        views.append({
            "id": item.id, "kind": item.kind, "name": item.name, "description": item.description,
            "material_id": item.material_id, "parent_item_id": item.parent_item_id,
            "spatial_object_id": item.spatial_object_id, "spatial_zone_id": item.spatial_zone_id,
            "library_definition_id": item.library_definition_id, "source": item.source,
            "reviewed_library_version": item.reviewed_library_version,
            "stale_reasons": reasons, "measurements": values, "specifications": item_specs,
        })
    return views, items, catalog


def get_record(db, scene_id, record_id, user_id):
    scene = _scene(db, scene_id, user_id)
    record = _record(db, scene, record_id)
    items, _, _ = _audit(db, record, scene)
    reasons = []
    if scene.deleted_at is not None:
        reasons.append("scene_deleted")
    if scene.current_revision != record.scene_revision:
        reasons.append("scene_revision_changed")
    if any(item["stale_reasons"] for item in items):
        reasons.append("item_sources_changed")
    materials = db.scalars(select(ProductionMaterial).where(ProductionMaterial.record_id == record.id)
                           .order_by(ProductionMaterial.name, ProductionMaterial.id)).all()
    return {**_record_view(record, scene), "stale": bool(reasons), "stale_reasons": reasons,
            "items": items, "materials": materials}


def _require_approver(db, scene, user_id):
    project = require_project_access(db, user_id, scene.project_id)
    if require_membership(db, user_id, project.organization_id).role not in ADMIN_ROLES:
        raise HTTPException(403, detail="Insufficient permissions")


def review_sources(db, scene_id, record_id, user_id, data, *, approve=False):
    scene = _scene(db, scene_id, user_id, write=True)
    if approve:
        _require_approver(db, scene, user_id)
    record = _draft(db, scene, record_id, data.expected_version)
    _check_scene_revision(scene, data.scene_revision)
    views, items, catalog = _audit(db, record, scene, lock_catalog=True)
    if approve and record.scene_revision != scene.current_revision:
        _conflict("production_sources_stale", "Review the production sources before approval.")
    blockers = {str(item["id"]): [
        reason for reason in item["stale_reasons"] if approve or reason != "library_definition_changed"
    ] for item in views}
    blockers = {item_id: reasons for item_id, reasons in blockers.items() if reasons}
    if blockers:
        _conflict("production_sources_stale", "Resolve stale item sources.", items=blockers)
    if approve:
        from app.services.production_geometry import ProductionGeometry
        from app.services.production_quantities import approval_blockers

        quantity_ids = approval_blockers(db, record, ProductionGeometry(db, scene))
        if quantity_ids:
            _conflict("production_quantities_unready", "Resolve invalid or stale quantities before approval.",
                      quantity_ids=quantity_ids)
        record.status, record.approved_by_user_id = "approved", user_id
        record.approved_at = datetime.now(timezone.utc)
    else:
        record.scene_revision = scene.current_revision
        for item in items:
            definition = catalog.get(item.library_definition_id)
            if definition is not None:
                item.reviewed_library_version = definition.version
    result = _save(db, record, scene=scene)
    if approve:
        _log("production_record_approved", record_id=str(result["id"]), scene_revision=data.scene_revision)
    return result


def _item(db, record, item_id):
    return _scoped_row(db, ProductionItem, item_id, ProductionItem.record_id == record.id)


def item_ancestors(item, items):
    parents = []
    parent = item.parent_item_id
    while parent is not None:
        if parent in parents or parent not in items:
            _conflict("production_hierarchy_invalid", "The production assembly hierarchy is invalid.")
        parents.append(parent)
        parent = items[parent].parent_item_id
    return parents


def _validate_material(db, record, material_id):
    if material_id is not None:
        _scoped_row(db, ProductionMaterial, material_id, ProductionMaterial.record_id == record.id)


def _item_sources(db, scene, user_id, data):
    obj = scene_row(db, SpatialObject, scene.id, data.spatial_object_id)
    zone = scene_row(db, SpatialZone, scene.id, data.spatial_zone_id)
    zone_id = zone.id if zone is not None else None
    definition_id = data.library_definition_id
    if obj is not None:
        assigned_zone = _object_zone(obj, lambda parent_id: scene_row(db, SpatialObject, scene.id, parent_id))
        if zone is not None and zone.id != assigned_zone:
            _invalid("The zone must match the source object's zone assignment.")
        zone_id = assigned_zone
        if obj.library_definition_id is not None:
            if definition_id is not None and definition_id != obj.library_definition_id:
                _invalid("The library definition must match the source object's placement.")
            definition_id = obj.library_definition_id
    source = {key: str(value) for key, value in {
        "spatial_object_id": data.spatial_object_id, "spatial_zone_id": zone_id,
        "library_definition_id": definition_id,
    }.items() if value is not None}
    library_version = None
    if definition_id is not None:
        project = require_project_access(db, user_id, scene.project_id)
        definition = scene_definition(db, user_id, project, definition_id)
        library_version = definition.version
        source["library_definition_version"] = definition.version
        if obj is not None and obj.library_definition_version is not None:
            source["placed_library_definition_version"] = obj.library_definition_version
    return zone_id, definition_id, library_version, source


def _validate_item_parent(db, record, parent_id, item_id):
    seen = {item_id}
    while parent_id is not None:
        if parent_id in seen:
            _invalid("An assembly cannot contain itself or one of its ancestors.")
        seen.add(parent_id)
        parent = _item(db, record, parent_id)
        if parent.kind != "assembly":
            _invalid("A parent item must be an assembly.")
        parent_id = parent.parent_item_id


def write_item(db, scene_id, record_id, user_id, data, *, item_id=None):
    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, data.expected_version)
    _validate_material(db, record, data.material_id)
    if item_id is None:
        _validate_item_parent(db, record, data.parent_item_id, None)
        zone_id, definition_id, library_version, source = _item_sources(db, scene, user_id, data)
        item = ProductionItem(
            id=uuid4(), record_id=record.id, scene_id=scene.id, kind=data.kind, parent_item_id=data.parent_item_id,
            spatial_object_id=data.spatial_object_id, spatial_zone_id=zone_id, library_definition_id=definition_id,
            reviewed_library_version=library_version, source=source,
        )
        db.add(item)
    else:
        item = _item(db, record, item_id)
        if "parent_item_id" in data.model_fields_set:
            _validate_item_parent(db, record, data.parent_item_id, item.id)
            item.parent_item_id = data.parent_item_id
    item.name, item.description, item.material_id = data.name.strip(), data.description, data.material_id
    return _save(db, record, item.id)


def delete_item(db, scene_id, record_id, item_id, user_id, expected_version):
    from app.services.production_specifications import require_no_approved_specs

    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, expected_version)
    item = _item(db, record, item_id)
    require_no_approved_specs(db, item_id=item.id)
    db.delete(item)
    return _save(db, record, item_id)


def write_material(db, scene_id, record_id, user_id, data, *, material_id=None):
    from app.services.production_materials import assignment_values

    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, data.expected_version)
    if material_id is None:
        row = ProductionMaterial(id=uuid4(), record_id=record.id)
    else:
        row = _scoped_row(db, ProductionMaterial, material_id, ProductionMaterial.record_id == record.id)
    definition_id, overrides = assignment_values(db, scene, user_id, data, row)
    values = data.name.strip(), data.description, definition_id, overrides
    if material_id is None:
        db.add(row)
    elif values != (row.name, row.description, row.definition_id, row.overrides):
        row.version += 1
    row.name, row.description, row.definition_id, row.overrides = values
    return _save(db, record, row.id)


def delete_material(db, scene_id, record_id, material_id, user_id, expected_version):
    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, expected_version)
    db.delete(_scoped_row(db, ProductionMaterial, material_id, ProductionMaterial.record_id == record.id))
    return _save(db, record, material_id)


def write_specification(db, scene_id, record_id, item_id, user_id, data, *, specification_id=None):
    from app.services.production_specifications import capture

    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, data.expected_version)
    item = _item(db, record, item_id)
    technical = capture(db, scene, record, item, data.technical, user_id) if data.technical is not None else None
    if specification_id is None:
        previous = None
        if data.supersedes_id is not None:
            previous = _scoped_row(db, ProductionSpecification, data.supersedes_id,
                                   ProductionSpecification.item_id == item.id)
            if previous.approved_at is None:
                _conflict("specification_not_approved", "Edit the existing draft specification.")
            if db.scalar(select(ProductionSpecification.id).where(ProductionSpecification.supersedes_id == previous.id)):
                _conflict("specification_already_superseded", "The approved specification already has a revision.")
            if previous.technical is not None and technical is None:
                _invalid("Supply technical fields when revising a structured specification.")
        row = ProductionSpecification(
            id=uuid4(), item_id=item.id, technical=technical,
            supersedes_id=previous.id if previous is not None else None,
            version=previous.version + 1 if previous is not None else 1,
        )
        db.add(row)
    else:
        row = _scoped_row(db, ProductionSpecification, specification_id, ProductionSpecification.item_id == item.id)
        if row.approved_at is not None:
            _conflict("specification_approved", "Create a revision of the approved specification.")
        if data.supersedes_id is not None:
            _invalid("A predecessor can only be set when creating a specification.")
        if technical is None:
            technical = row.technical
        if (row.name, row.content, row.technical) != (data.name.strip(), data.content, technical):
            row.version += 1
        row.technical = technical
    row.name, row.content = data.name.strip(), data.content
    return _save(db, record, row.id)


def delete_specification(db, scene_id, record_id, item_id, specification_id, user_id, expected_version):
    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, expected_version)
    item = _item(db, record, item_id)
    row = _scoped_row(db, ProductionSpecification, specification_id, ProductionSpecification.item_id == item.id)
    if row.approved_at is not None:
        _conflict("specification_approved", "The approved specification is retained in its revision history.")
    db.delete(row)
    return _save(db, record, specification_id)


def _geometry_value(db, scene, target, target_id, kind):
    if kind in ("weight", "count"):
        _invalid("Spatial geometry does not provide weight or count.")
    if target == "scene":
        if kind in _SPATIAL_DIMENSIONS:
            return getattr(scene, _SPATIAL_DIMENSIONS[kind])
        area = scene.width_m * scene.depth_m
        return area if kind == "area" else area * scene.height_m

    compilation = SceneCompilation(GraphRead(db, scene))
    runtime = compilation.compile()
    key = (target, target_id)
    node = next((node for node in runtime["nodes"] if (node["type"], node["id"]) == key), None)
    if node is None or node["geometry"] is None or key in compilation.invalid_nodes:
        _invalid("The source has no supported geometry.")
    geometry = node["geometry"]
    if kind in _SPATIAL_DIMENSIONS:
        return geometry["world_bounds"]["dimensions_m"][_SPATIAL_DIMENSIONS[kind]]
    footprint, world, dimensions = compilation.footprints[key], node["world_matrix"], node["authored_dimensions"]
    if kind == "area" and geometry["mode"] == "triangles":
        return geometry_area(project_xy(footprint, world, height_m=dimensions["height_m"] if dimensions else None))
    if kind == "volume" and geometry["kind"] == "box":
        extents = Vec3(dimensions["width_m"], dimensions["depth_m"], dimensions["height_m"])
        return Parallelepiped3.from_center_dimensions(Vec3(0, 0, 0), extents, world).volume
    _invalid("This geometry does not provide the requested measurement.")


def _measurement_source(db, scene, item, user_id, kind, data):
    source = data.source
    snapshot = source.model_dump(mode="json", exclude={"type", "specification_id", "recommendation_id"})
    specification_id = None
    value, unit = data.value, data.unit
    if source.type == "spatial_geometry":
        _check_scene_revision(scene, source.scene_revision)
        target_id = {"object": item.spatial_object_id, "zone": item.spatial_zone_id, "scene": scene.id}[source.target]
        if target_id is None:
            _invalid("The production item has no live reference for this target.")
        value, unit = _geometry_value(db, scene, source.target, target_id, kind), CANONICAL_UNITS[kind]
        snapshot["target_id"] = str(target_id)
    elif source.type == "object_library":
        if kind not in _SPATIAL_DIMENSIONS or item.library_definition_id is None:
            _invalid("Library measurements require a linked definition and an authored length, width, or height.")
        project = require_project_access(db, user_id, scene.project_id)
        definition = scene_definition(db, user_id, project, item.library_definition_id)
        if definition.status != "active" or definition.version != source.definition_version:
            _conflict("production_library_source_changed", "Reload the active library definition.",
                      current_version=definition.version)
        value, unit = getattr(definition, _SPATIAL_DIMENSIONS[kind]), CANONICAL_UNITS[kind]
        snapshot["library_definition_id"] = str(definition.id)
    elif source.type == "technical_specification":
        spec = _scoped_row(db, ProductionSpecification, source.specification_id, ProductionSpecification.item_id == item.id)
        if spec.version != source.specification_version:
            _conflict("production_specification_changed", "Reload the technical specification.", current_version=spec.version)
        specification_id = spec.id
    elif source.type == "ai_suggestion":
        review = db.get(SpatialDesignReview, (scene.id, source.recommendation_id), populate_existing=True)
        if review is None:
            _missing()
        if review.status == "rejected" or review.review_id != source.review_id:
            _conflict("production_ai_source_changed", "Reload the reviewed design recommendation.")
        revision = _review_scene_revision(review)
        _check_scene_revision(scene, revision)
        snapshot["scene_revision"] = revision
    return normalize(kind, Decimal(str(value)), unit), snapshot, specification_id


def write_measurement(db, scene_id, record_id, item_id, kind, user_id, data):
    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, data.expected_version)
    item = _item(db, record, item_id)
    row = db.get(ProductionMeasurement, (item.id, kind), populate_existing=True)
    if row is not None and row.source_type == "user_entered" and data.source.type != "user_entered" and not data.replace_manual:
        _conflict("production_manual_measurement", "Replacing a manual measurement requires explicit confirmation.")
    try:
        value, source, specification_id = _measurement_source(db, scene, item, user_id, kind, data)
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    if row is None:
        row = ProductionMeasurement(item_id=item.id, scene_id=scene.id, kind=kind)
        db.add(row)
    row.value, row.source, row.specification_id = value, source, specification_id
    row.source_type, row.entered_by_user_id = data.source.type, user_id
    row.recommendation_id = data.source.recommendation_id if data.source.type == "ai_suggestion" else None
    return _save(db, record, item.id)


def delete_measurement(db, scene_id, record_id, item_id, kind, user_id, expected_version):
    scene = _scene(db, scene_id, user_id, write=True)
    record = _draft(db, scene, record_id, expected_version)
    item = _item(db, record, item_id)
    row = db.get(ProductionMeasurement, (item.id, kind))
    if row is None:
        _missing()
    db.delete(row)
    return _save(db, record, item.id)


def assembly_breakdown(db, scene_id, record_id, item_id, user_id):
    detail = get_record(db, scene_id, record_id, user_id)
    items = {item["id"]: item for item in detail["items"]}
    if item_id not in items:
        _missing()
    children = defaultdict(list)
    for item in detail["items"]:
        children[item["parent_item_id"]].append(item["id"])
    stack, result, visited = [(item_id, 0)], [], set()
    while stack:
        current, depth = stack.pop()
        if current in visited:
            _conflict("production_hierarchy_invalid", "The production assembly hierarchy is invalid.")
        visited.add(current)
        result.append({**items[current], "depth": depth})
        stack.extend((child, depth + 1) for child in reversed(children[current]))
    return result
