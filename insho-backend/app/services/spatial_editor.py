"""Editor snapshots and atomic actions over the canonical spatial service."""

from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.spatial import SpatialObject, SpatialObjectAsset, SpatialObjectRelationship, SpatialSceneRevision, SpatialZone
from app.schemas.spatial import (
    SpatialAssetAttach, SpatialInput, SpatialObjectCreate, SpatialObjectUpdate,
    SpatialRelationshipCreate, SpatialSceneUpdate, SpatialZoneCreate, SpatialZoneUpdate,
)
from app.security import EDITOR_ROLES, require_membership, require_project_access
from app.services.spatial import (
    SpatialService, _commit, _flush, _get_scene, _missing, _object_response,
    _record_revision, _relationship_response, _row_state, _scene_response, _zone_response,
)
from app.services.spatial_geometry import _geometry_data, _item_data, _object_item, _resolve_world_matrices, _zone_footprint
from app.spatial.snapping import geometry_anchors
from app.spatial.types import GeometryError
from app.spatial_units import to_meters


MIN_DIMENSION = Decimal("0.000001")
MAX_COORDINATE = Decimal("99999999.999999")


class EditorOperation(SpatialInput):
    kind: Literal[
        "create_object", "update_object", "delete_object", "create_zone", "update_zone", "delete_zone",
        "update_scene", "attach_asset", "detach_asset", "create_relationship", "delete_relationship",
    ]
    id: UUID
    object_id: Optional[UUID] = None
    data: dict = Field(default_factory=dict)


class EditorChange(SpatialInput):
    base_revision: int = Field(ge=1)
    operations: list[EditorOperation] = Field(min_length=1, max_length=500)


def _rows(db, model, scene):
    return db.scalars(select(model).where(model.scene_id == scene.id).order_by(model.created_at, model.id)).all()


def _snapshot(db, scene, user_id):
    project = require_project_access(db, user_id, scene.project_id)
    membership = require_membership(db, user_id, project.organization_id)
    objects, zones = _rows(db, SpatialObject, scene), _rows(db, SpatialZone, scene)
    geometries = {}
    for rows, parent, is_object in ((zones, "parent_zone_id", False), (objects, "parent_object_id", True)):
        matrices = _resolve_world_matrices(rows, parent)
        for row in rows:
            matrix = matrices[row.id]
            output = {"matrix": matrix}
            try:
                if is_object and row.node_kind == "group":
                    geometries[str(row.id)] = output
                    continue
                if is_object:
                    item = _object_item(row, matrix)
                    output.update(_item_data(item))
                    output["anchors"] = [
                        {"kind": anchor.kind, "x_m": anchor.point.x, "y_m": anchor.point.y}
                        for anchor in geometry_anchors(str(row.id), item.footprint)
                    ]
                else:
                    footprint = _zone_footprint(row, matrices)
                    if footprint is not None:
                        output.update(_geometry_data(footprint))
            except GeometryError as exc:
                output["error"] = str(exc)
            geometries[str(row.id)] = output
    return {
        "scene": _scene_response(scene), "can_edit": membership.role in EDITOR_ROLES,
        "objects": [_object_response(db, row) for row in objects],
        "zones": [_zone_response(row) for row in zones], "geometry": geometries,
        "assets": [_row_state(row) for row in _rows(db, SpatialObjectAsset, scene)],
        "relationships": [_relationship_response(row) for row in _rows(db, SpatialObjectRelationship, scene)],
        "meters_per_unit": {unit: float(to_meters(1, unit)) for unit in ("m", "cm", "mm", "ft", "in")},
    }


def snapshot(db: Session, scene_id: UUID, user_id: UUID):
    return _snapshot(db, _get_scene(db, scene_id, user_id, lock="read"), user_id)


def _scoped(db, model, entity_id, scene_id):
    row = db.get(model, entity_id) if entity_id is not None else None
    if row is None or row.scene_id != scene_id:
        _missing()
    return row


def _validated(schema, data):
    try:
        result = schema.model_validate(data)
    except ValidationError as exc:
        issue = exc.errors()[0]
        raise HTTPException(422, detail=f"{'.'.join(map(str, issue['loc']))}: {issue['msg']}") from exc
    dimensions = getattr(result, "dimensions", None)
    transform = getattr(result, "transform", None)
    if dimensions and any(not MIN_DIMENSION <= value <= MAX_COORDINATE for value in dimensions.model_dump().values()):
        raise HTTPException(422, detail="Dimensions must fit the canonical meter precision and range.")
    if transform:
        for vector in transform.model_dump(exclude_unset=True).values():
            if any(abs(value) > MAX_COORDINATE for value in vector.values()):
                raise HTTPException(422, detail="Transform exceeds the canonical numeric range.")
        if any(value < MIN_DIMENSION for value in transform.scale.model_dump().values()):
            raise HTTPException(422, detail="Scale is below canonical precision.")
    return result


def _restored_objects(db, scene, operations):
    # Older editor clients recreate a deleted object without its layer or catalog provenance.
    ids = {op.id for op in operations if op.kind == "create_object"}
    if not ids:
        return {}
    revisions = SpatialSceneRevision
    rows = db.execute(select(revisions.entity_id, revisions.before_state).where(
        revisions.scene_id == scene.id, revisions.entity_type == "object",
        revisions.operation == "delete", revisions.entity_id.in_(ids),
    ).distinct(revisions.entity_id).order_by(revisions.entity_id, revisions.revision_number.desc())).all()
    return {row.entity_id: row.before_state for row in rows}


def _apply(db, scene, user_id, operation, restored_objects):
    kind, entity_id, data = operation.kind, operation.id, operation.data
    args = {"user_id": user_id, "commit": False}
    creates = {
        "create_object": (SpatialObjectCreate, SpatialService.create_object),
        "create_zone": (SpatialZoneCreate, SpatialService.create_zone),
        "create_relationship": (SpatialRelationshipCreate, SpatialService.create_relationship),
    }
    updates = {
        "update_object": (SpatialObject, SpatialObjectUpdate, SpatialService.update_object),
        "update_zone": (SpatialZone, SpatialZoneUpdate, SpatialService.update_zone),
    }
    deletes = {
        "delete_object": (SpatialObject, SpatialService.delete_object),
        "delete_zone": (SpatialZone, SpatialService.delete_zone),
        "delete_relationship": (SpatialObjectRelationship, SpatialService.delete_relationship),
    }
    if kind in creates:
        saved = restored_objects.get(entity_id) if kind == "create_object" else None
        if saved is not None:
            if "layer_id" not in data:
                data = {**data, "layer_id": saved.get("layer_id")}
            definition_id = saved.get("library_definition_id")
            if definition_id:
                args["library_origin"] = (UUID(definition_id), saved["library_definition_version"])
        schema, method = creates[kind]
        method(db, scene.id, _validated(schema, data), entity_id=entity_id, **args)
    elif kind in updates:
        model, schema, method = updates[kind]
        _scoped(db, model, entity_id, scene.id)
        method(db, entity_id, _validated(schema, data), **args)
    elif kind in deletes:
        model, method = deletes[kind]
        row = _scoped(db, model, entity_id, scene.id)
        if kind == "delete_object":
            restored_objects[entity_id] = _row_state(row)
        method(db, entity_id, **args)
    elif kind == "update_scene":
        if entity_id != scene.id:
            _missing()
        SpatialService.update_scene(db, scene.id, _validated(SpatialSceneUpdate, data), **args)
    elif kind == "attach_asset":
        _scoped(db, SpatialObject, operation.object_id, scene.id)
        SpatialService.attach_asset(db, operation.object_id, _validated(SpatialAssetAttach, data), entity_id=entity_id, **args)
    elif kind == "detach_asset":
        link = _scoped(db, SpatialObjectAsset, entity_id, scene.id)
        SpatialService.detach_asset(db, link.object_id, link.id, **args)


def apply_change(db: Session, scene_id: UUID, user_id: UUID, change_id: UUID, change: EditorChange):
    scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
    payload = json.dumps(change.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    digest = sha256(payload.encode()).hexdigest()
    prior = db.scalars(select(SpatialSceneRevision).where(
        SpatialSceneRevision.scene_id == scene.id, SpatialSceneRevision.entity_id == change_id,
        SpatialSceneRevision.entity_type == "editor_action",
    )).first()
    if prior:
        if prior.changed_by_user_id != user_id or prior.after_state["digest"] != digest:
            raise HTTPException(409, detail="This action ID is already in use.")
        return {"committed_revision": prior.revision_number, "snapshot": _snapshot(db, scene, user_id)}
    if scene.current_revision != change.base_revision:
        raise HTTPException(409, detail="The scene changed elsewhere. Reload the latest scene before editing.")
    try:
        restored_objects = _restored_objects(db, scene, change.operations)
        for operation in change.operations:
            _apply(db, scene, user_id, operation, restored_objects)
        receipt = _record_revision(db, scene, user_id, "commit", "editor_action", change_id, None, {"digest": digest})
        _flush(db)
        response = {"committed_revision": receipt.revision_number, "snapshot": _snapshot(db, scene, user_id)}
        _commit(db)
        return response
    except Exception:
        db.rollback()
        raise
