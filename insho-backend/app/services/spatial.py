"""Authorized, transactional operations for the canonical spatial scene graph."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Union
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.asset_derivative import AssetDerivative
from app.models.event import Event
from app.models.project import Project
from app.models.spatial import (
    SpatialLayer, SpatialObject, SpatialObjectAsset, SpatialObjectRelationship,
    SpatialScene, SpatialSceneRevision, SpatialZone,
)
from app.schemas.spatial import (
    ObjectDimensions, SceneDimensions, SpatialAssetAttach, SpatialGroupCreate, SpatialHierarchyNode, SpatialObjectCreate,
    SpatialObjectUpdate, SpatialRelationshipCreate, SpatialSceneCreate,
    SpatialSceneUpdate, SpatialTransform, SpatialZoneCreate, SpatialZoneUpdate,
)
from app.security import require_event_access, require_project_access
from app.services.asset import AssetService
from app.services.spatial_policy import (
    append_layer_member_order, append_order, default_layer, order_key, require_branch_unlocked, require_unlocked, scene_row,
)
from app.services.spatial_validation_config import stored_config


NOT_FOUND = "Resource not found"
_MIN_POSITIVE = Decimal("0.000001")
_MAX_STORED = Decimal("99999999.999999")


def _missing():
    raise HTTPException(status_code=404, detail=NOT_FOUND)


def asset_matches_scene(asset, project, scene):
    return (asset.organization_id == project.organization_id
            and (asset.project_id is None or asset.project_id == project.id)
            and (asset.workspace_id is None or asset.workspace_id == project.workspace_id)
            and (asset.event_id is None or asset.event_id == scene.event_id))


def _conflict(message="Spatial relationship conflicts with existing scene data"):
    raise HTTPException(status_code=409, detail=message)


def _json_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _row_state(row):
    if row is None:
        return None
    return {
        attribute.key: _json_value(getattr(row, attribute.key))
        for attribute in row.__mapper__.column_attrs
    }


def _transform_values(transform: SpatialTransform) -> dict:
    def get(container, key):
        return container[key] if isinstance(container, dict) else getattr(container, key)
    position = get(transform, "position")
    rotation = get(transform, "rotation_deg")
    scale = get(transform, "scale")
    values = {
        "position_x_m": get(position, "x"),
        "position_y_m": get(position, "y"),
        "position_z_m": get(position, "z"),
        "rotation_x_deg": get(rotation, "x"),
        "rotation_y_deg": get(rotation, "y"),
        "rotation_z_deg": get(rotation, "z"),
        "scale_x": get(scale, "x"),
        "scale_y": get(scale, "y"),
        "scale_z": get(scale, "z"),
    }
    if any(abs(value) > _MAX_STORED for value in values.values()):
        raise HTTPException(422, detail="Transform exceeds the canonical numeric range.")
    if any(values["scale_" + axis] < _MIN_POSITIVE for axis in ("x", "y", "z")):
        raise HTTPException(422, detail="Scale is below canonical precision.")
    return values


def _transform_response(row):
    return {
        "position": {"x": row.position_x_m, "y": row.position_y_m, "z": row.position_z_m},
        "rotation_deg": {"x": row.rotation_x_deg, "y": row.rotation_y_deg, "z": row.rotation_z_deg},
        "scale": {"x": row.scale_x, "y": row.scale_y, "z": row.scale_z},
    }


def _dimensions_values(dimensions) -> dict:
    def get(key):
        return dimensions[key] if isinstance(dimensions, dict) else getattr(dimensions, key)
    values = {key: get(key) for key in ("width_m", "depth_m", "height_m")}
    if any((isinstance(value, Decimal) and not value.is_finite())
           or not _MIN_POSITIVE <= value <= _MAX_STORED for value in values.values()):
        raise HTTPException(422, detail="Dimensions exceed the canonical numeric range or precision.")
    return values


def _merged_transform_values(row, patch: dict) -> dict:
    merged = _transform_response(row)
    for key, values in patch.items():
        merged[key].update(values)
    return _transform_values(SpatialTransform.model_validate(merged))


def _scene_response(scene):
    return {
        "id": scene.id, "project_id": scene.project_id, "event_id": scene.event_id,
        "name": scene.name, "description": scene.description, "scene_type": scene.scene_type,
        "display_unit": scene.display_unit, "coordinate_system": scene.coordinate_system,
        "dimensions": {"width_m": scene.width_m, "depth_m": scene.depth_m, "height_m": scene.height_m},
        "status": scene.status, "metadata": scene.metadata_json,
        "current_revision": scene.current_revision, "created_by_user_id": scene.created_by_user_id,
        "created_at": scene.created_at, "updated_at": scene.updated_at,
    }


def _zone_response(zone):
    dimensions = None
    if zone.width_m is not None:
        dimensions = {"width_m": zone.width_m, "depth_m": zone.depth_m, "height_m": zone.height_m}
    return {
        "id": zone.id, "scene_id": zone.scene_id, "parent_zone_id": zone.parent_zone_id,
        "order_index": zone.order_index, "is_locked": zone.is_locked,
        "name": zone.name, "category": zone.category, "description": zone.description,
        "transform": _transform_response(zone), "dimensions": dimensions,
        "footprint": zone.footprint, "appearance": zone.appearance,
        "metadata": zone.metadata_json, "status": zone.status,
        "created_at": zone.created_at, "updated_at": zone.updated_at,
    }


def _object_response(db: Session, obj):
    keys = ("width_m", "depth_m", "height_m")
    dimensions = None if obj.node_kind == "group" else {key: getattr(obj, key) for key in keys}
    effective = None if dimensions is None else {
        key: dimensions[key] * getattr(obj, "scale_" + axis) for key, axis in zip(keys, ("x", "y", "z"))
    }
    return {
        "id": obj.id, "scene_id": obj.scene_id, "zone_id": obj.zone_id,
        "library_definition_id": obj.library_definition_id, "library_definition_version": obj.library_definition_version,
        "parent_object_id": obj.parent_object_id, "object_type": obj.object_type,
        "layer_id": obj.layer_id, "layer_order_index": obj.layer_order_index, "node_kind": obj.node_kind, "order_index": obj.order_index, "is_locked": obj.is_locked,
        "category": obj.category, "name": obj.name, "transform": _transform_response(obj),
        "dimensions": dimensions, "effective_dimensions_m": effective,
        "appearance": obj.appearance, "material_refs": obj.material_refs,
        "properties": obj.properties, "tags": obj.tags, "status": obj.status,
        "created_at": obj.created_at, "updated_at": obj.updated_at,
    }


def _relationship_response(row):
    return {
        "id": row.id, "scene_id": row.scene_id,
        "source_object_id": row.source_object_id,
        "target_object_id": row.target_object_id,
        "relationship_type": row.relationship_type,
        "metadata": row.metadata_json, "created_at": row.created_at,
    }


def _validate_event_project(db: Session, user_id: UUID, project: Project, event_id: Optional[UUID], *, write=False):
    if event_id is None:
        return None
    event = require_event_access(db, user_id, event_id, write=write)
    if event.project_id != project.id or event.organization_id != project.organization_id:
        _missing()
    return event


def _get_scene(db: Session, scene_id: UUID, user_id: UUID, *, write=False, lock=False, include_deleted=False):
    statement = select(SpatialScene).where(SpatialScene.id == scene_id)
    if not include_deleted:
        statement = statement.where(SpatialScene.deleted_at.is_(None))
    if lock:
        statement = statement.with_for_update(read=lock == "read")
        statement = statement.execution_options(populate_existing=True)
    scene = db.scalars(statement).first()
    if scene is None:
        _missing()
    project = require_project_access(db, user_id, scene.project_id, write=write)
    _validate_event_project(db, user_id, project, scene.event_id, write=write)
    return scene


def _record_revision(db: Session, scene: SpatialScene, user_id: UUID, operation: str,
                     entity_type: str, entity_id: UUID, before=None, after=None):
    scene.current_revision += 1
    revision = SpatialSceneRevision(
        scene_id=scene.id,
        revision_number=scene.current_revision,
        changed_by_user_id=user_id,
        operation=operation,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=_json_value(before),
        after_state=_json_value(after),
    )
    db.add(revision)
    return revision


def _commit(db: Session, record=None, *, commit=True):
    if not commit:
        _flush(db)
        if record is not None:
            db.refresh(record)
        return record
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Spatial change violates scene relationship constraints") from exc
    if record is not None:
        db.refresh(record)
    return record


def _flush(db: Session):
    try:
        db.flush()
    except IntegrityError as exc:
        if not db.in_nested_transaction():
            db.rollback()
        raise HTTPException(status_code=409, detail="Spatial change violates scene relationship constraints") from exc


def _validate_patch_nulls(payload: dict, allowed_nulls: set):
    invalid = [key for key, value in payload.items() if value is None and key not in allowed_nulls]
    if invalid:
        raise HTTPException(status_code=422, detail="Fields cannot be null: " + ", ".join(sorted(invalid)))


def _zone(db: Session, zone_id: UUID, user_id: UUID, *, write=False, lock=False):
    zone = db.get(SpatialZone, zone_id)
    if zone is None:
        _missing()
    scene = _get_scene(db, zone.scene_id, user_id, write=write, lock=lock)
    if lock:
        zone = db.scalars(select(SpatialZone).where(SpatialZone.id == zone.id)
                         .execution_options(populate_existing=True)).first()
        if zone is None:
            _missing()
    return zone, scene


def _object(db: Session, object_id: UUID, user_id: UUID, *, write=False, lock=False):
    obj = db.get(SpatialObject, object_id)
    if obj is None:
        _missing()
    scene = _get_scene(db, obj.scene_id, user_id, write=write, lock=lock)
    if lock:
        obj = db.scalars(select(SpatialObject).where(SpatialObject.id == obj.id)
                         .execution_options(populate_existing=True)).first()
        if obj is None:
            _missing()
    return obj, scene


def _check_parent(db, model, parent_field, scene_id, node_id, parent_id):
    parent = scene_row(db, model, scene_id, parent_id)
    seen = {node_id}
    cursor = parent
    while cursor is not None:
        if cursor.id in seen:
            _conflict("The spatial hierarchy cannot contain a cycle.")
        seen.add(cursor.id)
        cursor = scene_row(db, model, scene_id, getattr(cursor, parent_field))
    return parent


def _check_zone_parent(db, scene_id, zone_id, parent_id):
    return _check_parent(db, SpatialZone, "parent_zone_id", scene_id, zone_id, parent_id)


def _check_object_parent(db, scene_id, object_id, parent_id):
    return _check_parent(db, SpatialObject, "parent_object_id", scene_id, object_id, parent_id)


class SpatialService:
    @staticmethod
    def create_scene(db: Session, project_id: UUID, data: SpatialSceneCreate, user_id: UUID):
        project = require_project_access(db, user_id, project_id, write=True)
        _validate_event_project(db, user_id, project, data.event_id, write=True)
        stored_config(data.metadata, "metadata")
        dimensions = _dimensions_values(data.dimensions)
        scene = SpatialScene(
            project_id=project.id, event_id=data.event_id, name=data.name.strip(),
            description=data.description, scene_type=data.scene_type.strip(),
            display_unit=data.display_unit, **dimensions,
            status=data.status, metadata_json=data.metadata, created_by_user_id=user_id,
            current_revision=1,
        )
        db.add(scene)
        _flush(db)
        db.add(SpatialSceneRevision(
            scene_id=scene.id, revision_number=1, changed_by_user_id=user_id,
            operation="create", entity_type="scene", entity_id=scene.id,
            before_state=None, after_state=_row_state(scene),
        ))
        _commit(db, scene)
        return _scene_response(scene)

    @staticmethod
    def list_scenes(db: Session, project_id: UUID, user_id: UUID, event_id: Optional[UUID] = None):
        project = require_project_access(db, user_id, project_id)
        if event_id is not None:
            _validate_event_project(db, user_id, project, event_id)
        stmt = select(SpatialScene).where(
            SpatialScene.project_id == project.id,
            SpatialScene.deleted_at.is_(None),
        )
        if event_id is not None:
            stmt = stmt.where(SpatialScene.event_id == event_id)
        rows = db.scalars(stmt.order_by(SpatialScene.created_at, SpatialScene.id)).all()
        return [_scene_response(row) for row in rows]

    @staticmethod
    def get_scene(db: Session, scene_id: UUID, user_id: UUID):
        return _scene_response(_get_scene(db, scene_id, user_id))

    @staticmethod
    def update_scene(db: Session, scene_id: UUID, data: SpatialSceneUpdate, user_id: UUID, *, commit=True):
        scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
        payload = data.model_dump(exclude_unset=True)
        _validate_patch_nulls(payload, {"description"})
        if "metadata" in payload:
            stored_config(payload["metadata"], "metadata")
        before = _row_state(scene)
        for key, value in payload.items():
            if key == "dimensions":
                dimensions = _dimensions_values(value)
                scene.width_m, scene.depth_m, scene.height_m = dimensions["width_m"], dimensions["depth_m"], dimensions["height_m"]
            elif key == "metadata":
                scene.metadata_json = value
            elif key == "name":
                scene.name = value.strip()
            elif key == "scene_type":
                scene.scene_type = value.strip()
            else:
                setattr(scene, key, value)
        _record_revision(db, scene, user_id, "update", "scene", scene.id, before, _row_state(scene))
        _commit(db, scene, commit=commit)
        return _scene_response(scene)

    @staticmethod
    def delete_scene(db: Session, scene_id: UUID, user_id: UUID):
        scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
        zones = db.scalar(select(func.count(SpatialZone.id)).where(SpatialZone.scene_id == scene.id)) or 0
        objects = db.scalar(select(func.count(SpatialObject.id)).where(SpatialObject.scene_id == scene.id)) or 0
        layers = db.scalar(select(func.count(SpatialLayer.id)).where(SpatialLayer.scene_id == scene.id)) or 0
        if zones or objects or layers:
            _conflict("A scene with zones, objects, or layers cannot be deleted")
        before = _row_state(scene)
        scene.status = "deleted"
        scene.deleted_at = datetime.now(timezone.utc)
        _record_revision(db, scene, user_id, "delete", "scene", scene.id, before, _row_state(scene))
        _commit(db)

    @staticmethod
    def create_zone(db: Session, scene_id: UUID, data: SpatialZoneCreate, user_id: UUID, *, commit=True, entity_id=None):
        scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
        zone = SpatialZone(
            id=entity_id, scene_id=scene.id, parent_zone_id=data.parent_zone_id,
            name=data.name.strip(), category=data.category.strip(), description=data.description,
            **_transform_values(data.transform),
            **(_dimensions_values(data.dimensions) if data.dimensions else {"width_m": None, "depth_m": None, "height_m": None}),
            footprint=[point.model_dump(mode="json") for point in data.footprint] if data.footprint else None,
            appearance=data.appearance, metadata_json=data.metadata, status=data.status,
        )
        require_unlocked(db, _check_zone_parent(db, scene.id, zone.id, zone.parent_zone_id))
        zone.order_index = append_order(db, scene.id, zone_parent=zone.parent_zone_id)
        db.add(zone)
        _flush(db)
        _record_revision(db, scene, user_id, "create", "zone", zone.id, None, _row_state(zone))
        _commit(db, zone, commit=commit)
        return _zone_response(zone)

    @staticmethod
    def list_zones(db: Session, scene_id: UUID, user_id: UUID):
        scene = _get_scene(db, scene_id, user_id)
        rows = db.scalars(select(SpatialZone).where(SpatialZone.scene_id == scene.id).order_by(SpatialZone.created_at, SpatialZone.id)).all()
        return [_zone_response(row) for row in rows]

    @staticmethod
    def get_zone(db: Session, zone_id: UUID, user_id: UUID):
        zone, _ = _zone(db, zone_id, user_id)
        return _zone_response(zone)

    @staticmethod
    def update_zone(db: Session, zone_id: UUID, data: SpatialZoneUpdate, user_id: UUID, *, commit=True):
        zone, scene = _zone(db, zone_id, user_id, write=True, lock=True)
        payload = data.model_dump(exclude_unset=True)
        _validate_patch_nulls(payload, {"parent_zone_id", "description", "footprint", "dimensions"})
        require_unlocked(db, zone)
        new_parent = payload.get("parent_zone_id", zone.parent_zone_id)
        parent_changed = new_parent != zone.parent_zone_id
        transform = _merged_transform_values(zone, payload["transform"]) if "transform" in payload else {}
        if parent_changed or any(getattr(zone, key) != value for key, value in transform.items()):
            require_branch_unlocked(db, zone)
        before = _row_state(zone)
        if parent_changed:
            require_unlocked(db, _check_zone_parent(db, scene.id, zone.id, new_parent))
            zone.order_index = append_order(db, scene.id, zone_parent=new_parent)
        for key, value in payload.items():
            if key == "transform":
                for attr, coordinate in transform.items():
                    setattr(zone, attr, coordinate)
            elif key == "dimensions":
                values = _dimensions_values(value) if value else {"width_m": None, "depth_m": None, "height_m": None}
                for attr, dimension in values.items():
                    setattr(zone, attr, dimension)
            elif key == "metadata":
                zone.metadata_json = value
            elif key in {"name", "category"}:
                setattr(zone, key, value.strip())
            elif key == "footprint":
                zone.footprint = _json_value(value)
            else:
                setattr(zone, key, value)
        _record_revision(db, scene, user_id, "update", "zone", zone.id, before, _row_state(zone))
        _commit(db, zone, commit=commit)
        return _zone_response(zone)

    @staticmethod
    def delete_zone(db: Session, zone_id: UUID, user_id: UUID, *, commit=True):
        zone, scene = _zone(db, zone_id, user_id, write=True, lock=True)
        require_unlocked(db, zone)
        child_count = db.scalar(select(func.count(SpatialZone.id)).where(SpatialZone.parent_zone_id == zone.id)) or 0
        object_count = db.scalar(select(func.count(SpatialObject.id)).where(SpatialObject.zone_id == zone.id)) or 0
        if child_count or object_count:
            _conflict("A zone with child zones or assigned objects cannot be deleted")
        before = _row_state(zone)
        _record_revision(db, scene, user_id, "delete", "zone", zone.id, before, None)
        db.delete(zone)
        _commit(db, commit=commit)

    @staticmethod
    def create_object(db: Session, scene_id: UUID, data: Union[SpatialObjectCreate, SpatialGroupCreate],
                      user_id: UUID, *, commit=True, entity_id=None, library_origin=None):
        scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
        group = isinstance(data, SpatialGroupCreate)
        zone = scene_row(db, SpatialZone, scene.id, data.zone_id)
        if group or "layer_id" in data.model_fields_set:
            layer = scene_row(db, SpatialLayer, scene.id, data.layer_id)
        else:
            layer = default_layer(db, scene.id)
        layer_id = layer.id if layer is not None else None
        parent = _check_object_parent(db, scene.id, entity_id, data.parent_object_id)
        require_unlocked(db, zone, layer, parent)
        obj = SpatialObject(
            id=entity_id, scene_id=scene.id, zone_id=data.zone_id, parent_object_id=data.parent_object_id,
            layer_id=layer_id, node_kind="group" if group else "object",
            layer_order_index=append_layer_member_order(db, scene.id, layer_id),
            order_index=append_order(db, scene.id, object_parent=data.parent_object_id, zone_parent=data.zone_id),
            object_type="group" if group else data.object_type.strip(), category=data.category.strip(), name=data.name.strip(),
            **_transform_values(data.transform), **({} if group else _dimensions_values(data.dimensions)),
            appearance=data.appearance, material_refs=[] if group else data.material_refs,
            properties=data.properties, tags=data.tags, status=data.status,
        )
        if library_origin is not None:
            from app.services.object_library import validate_linked_geometry

            obj.library_definition_id, obj.library_definition_version = library_origin
            validate_linked_geometry(db, obj)
        db.add(obj)
        _flush(db)
        _record_revision(db, scene, user_id, "create", obj.node_kind, obj.id, None, _row_state(obj))
        _commit(db, obj, commit=commit)
        return _object_response(db, obj)

    @staticmethod
    def list_objects(db: Session, scene_id: UUID, user_id: UUID, zone_id: Optional[UUID] = None):
        scene = _get_scene(db, scene_id, user_id)
        stmt = select(SpatialObject).where(SpatialObject.scene_id == scene.id)
        if zone_id is not None:
            if db.scalars(select(SpatialZone.id).where(SpatialZone.id == zone_id, SpatialZone.scene_id == scene.id)).first() is None:
                _missing()
            stmt = stmt.where(SpatialObject.zone_id == zone_id)
        rows = db.scalars(stmt.order_by(SpatialObject.created_at, SpatialObject.id)).all()
        return [_object_response(db, row) for row in rows]

    @staticmethod
    def get_object(db: Session, object_id: UUID, user_id: UUID):
        obj, _ = _object(db, object_id, user_id)
        return _object_response(db, obj)

    @staticmethod
    def update_object(db: Session, object_id: UUID, data: SpatialObjectUpdate, user_id: UUID, *, commit=True):
        obj, scene = _object(db, object_id, user_id, write=True, lock=True)
        payload = data.model_dump(exclude_unset=True)
        _validate_patch_nulls(payload, {"zone_id", "parent_object_id", "layer_id"})
        require_unlocked(db, obj)
        if obj.node_kind == "group" and ("dimensions" in payload or ("object_type" in payload and payload["object_type"] != "group")):
            raise HTTPException(422, detail="A group has a transform but has no physical dimensions.")
        parent_id = payload.get("parent_object_id", obj.parent_object_id)
        zone_id = payload.get("zone_id", obj.zone_id)
        layer_id = payload.get("layer_id", obj.layer_id)
        parent_changed = parent_id != obj.parent_object_id
        zone_changed = zone_id != obj.zone_id
        layer_changed = layer_id != obj.layer_id
        transform = _merged_transform_values(obj, payload["transform"]) if "transform" in payload else {}
        if parent_changed or zone_changed or layer_changed or any(getattr(obj, key) != value for key, value in transform.items()):
            require_branch_unlocked(db, obj)
            require_unlocked(
                db, _check_object_parent(db, scene.id, obj.id, parent_id),
                scene_row(db, SpatialZone, scene.id, zone_id), scene_row(db, SpatialLayer, scene.id, layer_id),
            )
        if (obj.library_definition_id and "properties" in payload and "geometry" in obj.properties
                and "geometry" not in payload["properties"]):
            payload["properties"] = {"geometry": obj.properties["geometry"], **payload["properties"]}
        before = _row_state(obj)
        if parent_changed or (zone_changed and parent_id is None):
            obj.order_index = append_order(db, scene.id, object_parent=parent_id, zone_parent=zone_id)
        if layer_changed:
            obj.layer_order_index = append_layer_member_order(db, scene.id, layer_id)
        for key, value in payload.items():
            if key == "transform":
                for attr, coordinate in transform.items():
                    setattr(obj, attr, coordinate)
            elif key == "dimensions":
                for attr, dimension in _dimensions_values(value).items():
                    setattr(obj, attr, dimension)
            elif key in {"name", "object_type", "category"}:
                setattr(obj, key, value.strip())
            else:
                setattr(obj, key, value)
        if obj.library_definition_id and (parent_changed or {"transform", "dimensions", "properties"} & payload.keys()):
            from app.services.object_library import validate_linked_geometry

            try:
                validate_linked_geometry(db, obj)
            except Exception:
                db.rollback()
                raise
        _record_revision(db, scene, user_id, "update", obj.node_kind, obj.id, before, _row_state(obj))
        _commit(db, obj, commit=commit)
        return _object_response(db, obj)

    @staticmethod
    def delete_object(db: Session, object_id: UUID, user_id: UUID, *, commit=True):
        obj, scene = _object(db, object_id, user_id, write=True, lock=True)
        require_unlocked(db, obj)
        child_count = db.scalar(select(func.count(SpatialObject.id)).where(SpatialObject.parent_object_id == obj.id)) or 0
        if child_count:
            _conflict("An object with child objects cannot be deleted")
        before = _row_state(obj)
        before["relationships"] = [
            _row_state(edge) for edge in db.scalars(select(SpatialObjectRelationship).where(
                (SpatialObjectRelationship.source_object_id == obj.id) |
                (SpatialObjectRelationship.target_object_id == obj.id)
            )).all()
        ]
        for edge in before["relationships"]:
            other = edge["target_object_id"] if edge["source_object_id"] == str(obj.id) else edge["source_object_id"]
            require_unlocked(db, scene_row(db, SpatialObject, scene.id, UUID(other)))
        before["assets"] = [
            _row_state(link) for link in db.scalars(select(SpatialObjectAsset).where(SpatialObjectAsset.object_id == obj.id)).all()
        ]
        _record_revision(db, scene, user_id, "delete", obj.node_kind, obj.id, before, None)
        db.delete(obj)
        _commit(db, commit=commit)

    @staticmethod
    def create_relationship(db: Session, scene_id: UUID, data: SpatialRelationshipCreate, user_id: UUID, *, commit=True, entity_id=None):
        scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
        if data.source_object_id == data.target_object_id:
            _conflict("An object cannot have a relationship to itself")
        require_unlocked(db, scene_row(db, SpatialObject, scene.id, data.source_object_id),
                         scene_row(db, SpatialObject, scene.id, data.target_object_id))
        edge = SpatialObjectRelationship(
            id=entity_id, scene_id=scene.id, source_object_id=data.source_object_id,
            target_object_id=data.target_object_id, relationship_type=data.relationship_type.strip().lower(),
            metadata_json=data.metadata,
        )
        db.add(edge)
        _flush(db)
        _record_revision(db, scene, user_id, "create", "relationship", edge.id, None, _row_state(edge))
        _commit(db, edge, commit=commit)
        return _relationship_response(edge)

    @staticmethod
    def list_relationships(db: Session, scene_id: UUID, user_id: UUID):
        scene = _get_scene(db, scene_id, user_id)
        rows = db.scalars(select(SpatialObjectRelationship).where(
            SpatialObjectRelationship.scene_id == scene.id,
        ).order_by(SpatialObjectRelationship.created_at, SpatialObjectRelationship.id)).all()
        return [_relationship_response(row) for row in rows]

    @staticmethod
    def delete_relationship(db: Session, relationship_id: UUID, user_id: UUID, *, commit=True):
        edge = db.get(SpatialObjectRelationship, relationship_id)
        if edge is None:
            _missing()
        scene = _get_scene(db, edge.scene_id, user_id, write=True, lock=True)
        edge = db.scalars(select(SpatialObjectRelationship).where(SpatialObjectRelationship.id == relationship_id)
                          .execution_options(populate_existing=True)).first()
        if edge is None:
            _missing()
        require_unlocked(db, scene_row(db, SpatialObject, scene.id, edge.source_object_id),
                         scene_row(db, SpatialObject, scene.id, edge.target_object_id))
        before = _row_state(edge)
        _record_revision(db, scene, user_id, "delete", "relationship", edge.id, before, None)
        db.delete(edge)
        _commit(db, commit=commit)

    @staticmethod
    def attach_asset(db: Session, object_id: UUID, data: SpatialAssetAttach, user_id: UUID, *, commit=True, entity_id=None):
        obj, scene = _object(db, object_id, user_id, write=True, lock=True)
        require_unlocked(db, obj)
        project = require_project_access(db, user_id, scene.project_id, write=True)
        asset = AssetService.get(db, data.asset_id, user_id, read_lock=True)
        if asset.status != "active" or not asset_matches_scene(asset, project, scene):
            _missing()
        meta = {
            "category": asset.category,
            "mime_type": asset.mime_type,
            "file_extension": asset.file_extension,
            "source": asset.storage_provider,
            "media_metadata": asset.media_metadata or {},
        }
        derivatives = db.scalars(select(AssetDerivative).where(AssetDerivative.asset_id == asset.id)).all()
        meta["previews"] = [{"kind": derivative.kind, "mime_type": derivative.mime_type} for derivative in derivatives]
        link = SpatialObjectAsset(
            id=entity_id, scene_id=scene.id, object_id=obj.id, asset_id=asset.id,
            role=data.role, asset_metadata=meta,
        )
        db.add(link)
        _flush(db)
        _record_revision(db, scene, user_id, "create", "asset_reference", link.id, None, _row_state(link))
        _commit(db, link, commit=commit)
        return _row_state(link)

    @staticmethod
    def list_assets(db: Session, object_id: UUID, user_id: UUID):
        obj, _ = _object(db, object_id, user_id)
        rows = db.scalars(select(SpatialObjectAsset).where(
            SpatialObjectAsset.object_id == obj.id,
        ).order_by(SpatialObjectAsset.created_at, SpatialObjectAsset.id)).all()
        return [_row_state(row) for row in rows]

    @staticmethod
    def detach_asset(db: Session, object_id: UUID, link_id: UUID, user_id: UUID, *, commit=True):
        obj, scene = _object(db, object_id, user_id, write=True, lock=True)
        require_unlocked(db, obj)
        link = db.scalars(select(SpatialObjectAsset).where(
            SpatialObjectAsset.id == link_id,
            SpatialObjectAsset.object_id == obj.id,
            SpatialObjectAsset.scene_id == scene.id,
        )).first()
        if link is None:
            _missing()
        before = _row_state(link)
        _record_revision(db, scene, user_id, "delete", "asset_reference", link.id, before, None)
        db.delete(link)
        _commit(db, commit=commit)

    @staticmethod
    def revisions(db: Session, scene_id: UUID, user_id: UUID, *, offset=0, limit=100):
        scene = _get_scene(db, scene_id, user_id)
        rows = db.scalars(select(SpatialSceneRevision).where(
            SpatialSceneRevision.scene_id == scene.id,
        ).order_by(SpatialSceneRevision.revision_number.desc()).offset(offset).limit(limit)).all()
        return [_row_state(row) for row in rows]

    @staticmethod
    def hierarchy(db: Session, scene_id: UUID, user_id: UUID):
        scene = _get_scene(db, scene_id, user_id, lock="read")
        zones = db.scalars(select(SpatialZone).where(SpatialZone.scene_id == scene.id)).all()
        objects = db.scalars(select(SpatialObject).where(SpatialObject.scene_id == scene.id)).all()
        zone_nodes = {
            row.id: {"id": row.id, "name": row.name, "node_type": "zone", "entity": _zone_response(row), "children": []}
            for row in zones
        }
        object_nodes = {
            row.id: {"id": row.id, "name": row.name, "node_type": row.node_kind, "entity": _object_response(db, row), "children": []}
            for row in objects
        }
        root = {"id": scene.id, "name": scene.name, "node_type": "scene", "entity": _scene_response(scene), "children": []}
        for row in sorted([*zones, *objects], key=order_key):
            if isinstance(row, SpatialZone):
                node = zone_nodes[row.id]
                parent = zone_nodes.get(row.parent_zone_id) if row.parent_zone_id else root
            else:
                node = object_nodes[row.id]
                if row.parent_object_id:
                    parent = object_nodes.get(row.parent_object_id)
                elif row.zone_id:
                    parent = zone_nodes.get(row.zone_id)
                else:
                    parent = root
            if parent is None:
                _conflict("The scene hierarchy has a missing reference. Inspect the scene validation report.")
            parent["children"].append(node)
        remaining = [root]
        reachable = 0
        while remaining:
            node = remaining.pop()
            reachable += 1
            remaining.extend(node["children"])
        if reachable != len(zones) + len(objects) + 1:
            _conflict("The scene hierarchy has unreachable nodes. Inspect the scene validation report.")
        try:
            return SpatialHierarchyNode.model_validate(root)
        except ValidationError as exc:
            raise HTTPException(409, detail={"code": "nested_hierarchy_unavailable",
                                             "message": "Use the flat scene graph endpoint for this hierarchy."}) from exc
