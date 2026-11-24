"""Database adapter between canonical INSHO spatial rows and pure geometry."""

from datetime import datetime, timezone
import math
from typing import Dict, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.spatial import SpatialObject, SpatialZone
from app.services.spatial_policy import StateResolver
from app.services.spatial import NOT_FOUND, _get_scene
from app.spatial.alignment import align, distribute_equal_spacing
from app.spatial.geometry2d import (
    Circle2, Geometry2, Polygon2, Rectangle2, Segment2, contains,
    geometry_area, geometry_bounds, geometry_center, geometry_perimeter,
    intersection, minimum_distance, polygon_vertices,
)
from app.spatial.geometry3d import Parallelepiped3
from app.spatial.math3d import (
    basis_vectors, resolve_world_matrices, transform_matrix,
)
from app.spatial.projection import project_xy
from app.spatial.snapping import Grid2, geometry_anchors, snap_point
from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Transform3, UnsupportedGeometryError, Vec2, Vec3
from app.spatial.validation import (
    GeometryItem, clearance_result, detect_overlaps, evaluate_boundary,
    evaluate_constraint, scene_boundary,
)


def _float(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GeometryError("Spatial values must be finite numbers") from exc
    if not math.isfinite(result):
        raise GeometryError("Spatial values must be finite")
    return result


def _local_transform(row) -> Transform3:
    return Transform3(
        position=Vec3(_float(row.position_x_m), _float(row.position_y_m), _float(row.position_z_m)),
        rotation_deg=Vec3(_float(row.rotation_x_deg), _float(row.rotation_y_deg), _float(row.rotation_z_deg)),
        scale=Vec3(_float(row.scale_x), _float(row.scale_y), _float(row.scale_z)),
    )


def _resolve_world_matrices(rows, parent_key: str, *, errors=None) -> Dict[UUID, tuple]:
    matrices = {}
    for row in rows:
        try:
            matrices[row.id] = transform_matrix(_local_transform(row))
        except GeometryError as exc:
            if errors is None:
                raise
            errors[row.id] = str(exc)

    def report(row_id, _code, message):
        errors[row_id] = message

    return resolve_world_matrices(
        matrices,
        {row.id: getattr(row, parent_key) for row in rows},
        report=report if errors is not None else None,
    )

def _point2(point):
    if not isinstance(point, dict) or not {"x_m", "y_m"} <= point.keys():
        raise GeometryError("Geometry points require x_m and y_m coordinates")
    return Vec2(_float(point["x_m"]), _float(point["y_m"]))


def _object_geometry_type(obj) -> tuple:
    properties = obj.properties if obj.properties is not None else {}
    if not isinstance(properties, dict):
        raise GeometryError("Object properties must be an object")
    definition = properties.get("geometry", {})
    if not isinstance(definition, dict):
        raise GeometryError("properties.geometry must be an object")
    geometry_type = str(definition.get("type", properties.get("geometry_type", "box"))).strip().lower()
    return geometry_type, definition


def _require_inside_extents(points, width, depth, label):
    if any(abs(point.x) > width / 2 + DEFAULT_TOLERANCE.linear(width)
           or abs(point.y) > depth / 2 + DEFAULT_TOLERANCE.linear(depth) for point in points):
        raise GeometryError(f"{label} footprint exceeds the object's authored dimensions")


def _object_local_footprint(obj) -> Geometry2:
    geometry_type, definition = _object_geometry_type(obj)
    width, depth = _float(obj.width_m), _float(obj.depth_m)
    if width <= 0 or depth <= 0:
        raise GeometryError("Object footprint dimensions must be positive")
    center = Vec2(0, 0)
    if geometry_type in {"box", "rectangle", "rect"}:
        return Rectangle2(center, width, depth)
    if geometry_type == "circle":
        radius = _float(definition.get("radius_m", obj.properties.get("radius_m", width / 2)))
        if radius > width / 2 + DEFAULT_TOLERANCE.linear(width) or radius > depth / 2 + DEFAULT_TOLERANCE.linear(depth):
            raise GeometryError("Circle footprint exceeds the object's authored dimensions")
        return Circle2(center, radius)
    if geometry_type == "point":
        return center
    if geometry_type in {"line", "line_segment"}:
        start = _point2(definition.get("start", {"x_m": -width / 2, "y_m": 0}))
        end = _point2(definition.get("end", {"x_m": width / 2, "y_m": 0}))
        _require_inside_extents((start, end), width, depth, "Line")
        return Segment2(start, end)
    if geometry_type == "polygon":
        points = definition.get("vertices", obj.properties.get("footprint"))
        if not isinstance(points, list):
            raise GeometryError("Polygon geometry requires a vertices array")
        local_points = tuple(_point2(point) for point in points)
        _require_inside_extents(local_points, width, depth, "Polygon")
        return Polygon2(local_points)
    raise UnsupportedGeometryError(f"Unsupported object geometry type: {geometry_type}")


def _object_footprint(obj, matrix) -> Geometry2:
    footprint = _object_local_footprint(obj)
    # Part 2 uses section footprints for custom shapes when measuring clearances.
    height = _float(obj.height_m) if isinstance(footprint, Rectangle2) else None
    return project_xy(footprint, matrix, height_m=height)


def _object_item(obj, world_matrix, *, projected=None, world_bounds=None) -> GeometryItem:
    if obj.node_kind == "group":
        raise GeometryError("A group has no physical geometry.")
    if world_matrix is None:
        raise GeometryError("Object world transform is unavailable.")
    geometry_type, _ = _object_geometry_type(obj)
    footprint = _object_footprint(obj, world_matrix) if projected is None else projected
    volume = Parallelepiped3.from_center_dimensions(
        Vec3(0.0, 0.0, 0.0),
        Vec3(_float(obj.width_m), _float(obj.depth_m), _float(obj.height_m)),
        world_matrix,
    )
    x_axis, y_axis, z_axis = basis_vectors(world_matrix)
    # The local vertical axis may point downward after a 180-degree flip; still upright.
    eps = DEFAULT_TOLERANCE.relative * 10
    upright = (
        math.hypot(z_axis.x, z_axis.y) <= eps * abs(z_axis.z)
        and all(abs(axis.z) <= eps * math.hypot(axis.x, axis.y) for axis in (x_axis, y_axis))
    )
    return GeometryItem(str(obj.id), geometry_type, footprint, volume, upright, world_bounds)


def _zone_local_footprint(zone) -> Optional[Geometry2]:
    if zone.footprint is not None:
        if not isinstance(zone.footprint, list):
            raise GeometryError("Zone footprint must be a vertices array")
        return Polygon2(tuple(_point2(point) for point in zone.footprint))
    if zone.width_m is None or zone.depth_m is None:
        return None
    return Rectangle2(Vec2(0, 0), _float(zone.width_m), _float(zone.depth_m))


def _zone_footprint(zone, matrices) -> Optional[Geometry2]:
    footprint = _zone_local_footprint(zone)
    return None if footprint is None else project_xy(footprint, matrices[zone.id])


def _geometry_data(geometry: Geometry2) -> dict:
    bounds = geometry_bounds(geometry)
    result = {
        "geometry_type": ("point" if isinstance(geometry, Vec2) else
                          "line_segment" if isinstance(geometry, Segment2) else
                          "circle" if isinstance(geometry, Circle2) else
                          "rectangle" if isinstance(geometry, Rectangle2) else "polygon"),
        "center": {"x_m": geometry_center(geometry).x, "y_m": geometry_center(geometry).y},
        "bounds": {"min_x_m": bounds.min_x, "min_y_m": bounds.min_y,
                    "max_x_m": bounds.max_x, "max_y_m": bounds.max_y,
                    "width_m": bounds.width, "depth_m": bounds.height},
    }
    if isinstance(geometry, Circle2):
        result["radius_m"] = geometry.radius
    elif isinstance(geometry, Segment2):
        result["vertices"] = [{"x_m": geometry.start.x, "y_m": geometry.start.y},
                              {"x_m": geometry.end.x, "y_m": geometry.end.y}]
    elif isinstance(geometry, Vec2):
        result["vertices"] = [{"x_m": geometry.x, "y_m": geometry.y}]
    else:
        result["vertices"] = [{"x_m": point.x, "y_m": point.y} for point in polygon_vertices(geometry)]
        if isinstance(geometry, Rectangle2):
            result["oriented_bounds"] = {
                "center": result["center"], "width_m": geometry.width,
                "depth_m": geometry.height, "rotation_deg": geometry.rotation_deg,
            }
    result["area_m2"] = _float(geometry_area(geometry))
    result["perimeter_m"] = _float(geometry_perimeter(geometry))
    return result


def _item_data(item: GeometryItem) -> dict:
    box = item.aabb
    return {
        **_geometry_data(item.footprint),
        "object_id": item.id,
        "center_3d": {"x_m": item.volume.center.x, "y_m": item.volume.center.y, "z_m": item.volume.center.z},
        "upright": item.upright,
        "volume_m3": _float(item.volume.volume),
        "volume_corners": [{"x_m": point.x, "y_m": point.y, "z_m": point.z} for point in item.volume.vertices],
        "bounds_3d": {"min_x_m": box.min_x, "min_y_m": box.min_y, "min_z_m": box.min_z,
                       "max_x_m": box.max_x, "max_y_m": box.max_y, "max_z_m": box.max_z,
                       "width_m": box.dimensions.x, "depth_m": box.dimensions.y, "height_m": box.dimensions.z},
    }


class SpatialGeometryService:
    @staticmethod
    def _load_scene(db: Session, scene_id: UUID, user_id: UUID, *, transform_errors=None):
        scene = _get_scene(db, scene_id, user_id, lock="read")
        objects = db.scalars(select(SpatialObject).where(SpatialObject.scene_id == scene.id)
                             .order_by(SpatialObject.order_index, SpatialObject.id)).all()
        zones = db.scalars(select(SpatialZone).where(SpatialZone.scene_id == scene.id)
                           .order_by(SpatialZone.order_index, SpatialZone.id)).all()
        object_errors, zone_errors = ({}, {}) if transform_errors is None else transform_errors
        object_matrices = _resolve_world_matrices(objects, "parent_object_id", errors=object_errors)
        zone_matrices = _resolve_world_matrices(zones, "parent_zone_id", errors=zone_errors)
        return scene, objects, zones, object_matrices, zone_matrices

    @classmethod
    def object_geometry(cls, db: Session, scene_id: UUID, object_id: UUID, user_id: UUID):
        scene, objects, _, matrices, _ = cls._load_scene(db, scene_id, user_id)
        obj = next((row for row in objects if row.id == object_id), None)
        if obj is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        item = _object_item(obj, matrices.get(obj.id))
        return {"scene_id": scene.id, "scene_revision": scene.current_revision,
                "transform_matrix": [list(row) for row in matrices[obj.id]],
                **_item_data(item)}

    @classmethod
    def validate_scene(cls, db: Session, scene_id: UUID, user_id: UUID,
                       constraints=(), tolerance: Tolerance = DEFAULT_TOLERANCE):
        object_errors, zone_errors = {}, {}
        scene, objects, zones, matrices, zone_matrices = cls._load_scene(
            db, scene_id, user_id, transform_errors=(object_errors, zone_errors),
        )
        boundary = scene_boundary(_float(scene.width_m), _float(scene.depth_m), _float(scene.height_m))
        items, item_by_id, errors, boundary_checks = [], {}, [], []
        for obj in objects:
            if obj.node_kind == "group":
                if obj.id in object_errors:
                    errors.append({"object_id": str(obj.id), "status": "invalid", "reason": object_errors[obj.id],
                                   "constraint_type": "valid_geometry"})
                continue
            try:
                if obj.id in object_errors:
                    raise GeometryError(object_errors[obj.id])
                item = _object_item(obj, matrices.get(obj.id))
                items.append(item)
                item_by_id[item.id] = item
                result = evaluate_boundary(item, boundary, tolerance)
                boundary_checks.append({"object_id": item.id, "status": result.status,
                                        "contained": result.contained,
                                        "bounds_3d": _item_data(item)["bounds_3d"],
                                        "tolerance_m": result.tolerance_m})
            except GeometryError as exc:
                errors.append({"object_id": str(obj.id), "status": "invalid",
                               "reason": str(exc), "constraint_type": "valid_geometry"})
                boundary_checks.append({"object_id": str(obj.id), "status": "unknown",
                                        "contained": None, "reason": "Object geometry is invalid."})

        zone_geometries = {}
        zone_checks = []
        for zone in zones:
            try:
                if zone.id in zone_errors:
                    raise GeometryError(zone_errors[zone.id])
                footprint = _zone_footprint(zone, zone_matrices)
                if footprint is not None:
                    zone_geometries[str(zone.id)] = footprint
            except GeometryError as exc:
                zone_checks.append({"zone_id": str(zone.id), "status": "invalid", "reason": str(exc)})

        for obj in objects:
            item = item_by_id.get(str(obj.id))
            if item is None or obj.zone_id is None:
                continue
            zone = zone_geometries.get(str(obj.zone_id))
            if zone is None:
                zone_checks.append({"object_id": str(obj.id), "zone_id": str(obj.zone_id),
                                    "status": "unknown", "reason": "Assigned zone has no measurable footprint."})
            else:
                contained = contains(zone, item.footprint, tolerance)
                zone_checks.append({"object_id": str(obj.id), "zone_id": str(obj.zone_id),
                                    "status": "inside" if contained else "outside", "contained": contained,
                                    "tolerance_m": tolerance.linear_m})

        overlaps = detect_overlaps(items, tolerance)
        overlap_results = [{key: value for key, value in finding.items() if key != "planar_result"} for finding in overlaps]
        constraint_results = []
        for constraint in constraints:
            values = constraint.model_dump() if hasattr(constraint, "model_dump") else dict(constraint)
            zone_values = zone_geometries
            check_tolerance = Tolerance(float(values["tolerance_m"]), tolerance.relative) if values.get("tolerance_m") else tolerance
            result = evaluate_constraint(values, objects=item_by_id, zones=zone_values,
                                         scene_boundary_geometry=boundary, tolerance=check_tolerance)
            constraint_results.append(result)
        statuses = [check["status"] for check in boundary_checks] + [check["status"] for check in zone_checks]
        statuses += [result["status"] for result in constraint_results]
        if errors or any(status in {"partial", "outside", "invalid", "fail"} for status in statuses):
            overall = "fail"
        elif any(status == "unknown" for status in statuses):
            overall = "unknown"
        else:
            overall = "pass"
        return {
            "scene_id": scene.id,
            "scene_revision": scene.current_revision,
            "evaluated_at": datetime.now(timezone.utc),
            "status": overall,
            "boundary_checks": boundary_checks,
            "zone_checks": zone_checks,
            "overlaps": overlap_results,
            "constraints": constraint_results,
            "invalid_geometries": errors,
            "tolerance_m": tolerance.linear_m,
        }

    @classmethod
    def clearance(cls, db: Session, scene_id: UUID, first_id: UUID, second_id: UUID,
                  user_id: UUID, requested_m: float, tolerance: Tolerance = DEFAULT_TOLERANCE):
        _, objects, _, matrices, _ = cls._load_scene(db, scene_id, user_id)
        by_id = {row.id: row for row in objects}
        first, second = by_id.get(first_id), by_id.get(second_id)
        if first is None or second is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return clearance_result(_object_item(first, matrices.get(first.id)),
                                _object_item(second, matrices.get(second.id)),
                                requested_m, tolerance)

    @classmethod
    def snap(cls, db: Session, scene_id: UUID, user_id: UUID, point: Vec2,
             spacing_m: Optional[float], anchor_tolerance_m: float,
             exclude_object_id: Optional[UUID] = None, exclude_object_ids=(), include_hidden: bool = True,
             use_anchors: bool = True):
        scene = _get_scene(db, scene_id, user_id, lock="read")
        anchors = []
        if use_anchors:
            objects = db.scalars(select(SpatialObject).where(SpatialObject.scene_id == scene.id)).all()
            matrices = _resolve_world_matrices(objects, "parent_object_id", errors={})
            excluded = set(exclude_object_ids) | {exclude_object_id}
            by_id = {obj.id: obj for obj in objects}
            settings = (scene.metadata_json or {}).get("editor")
            hidden = settings.get("hidden", []) if isinstance(settings, dict) else []
            hidden_categories = {key for key in hidden if isinstance(key, str)} if isinstance(hidden, list) else set()

            resolver = StateResolver(db.get)

            def eligible(obj):
                if obj.node_kind == "group" or (not include_hidden and not resolver.resolve(obj).visible):
                    return False
                while obj is not None:
                    if obj.id in excluded:
                        return False
                    if not include_hidden and (obj.status != "active" or f"object:{obj.category}" in hidden_categories):
                        return False
                    obj = by_id.get(obj.parent_object_id)
                return True

            for obj in objects:
                if obj.id not in matrices or not eligible(obj):
                    continue
                try:
                    item = _object_item(obj, matrices[obj.id])
                    anchors.extend(geometry_anchors(str(obj.id), item.footprint))
                except GeometryError:
                    continue
        grid = Grid2(spacing_m) if spacing_m is not None else None
        return snap_point(point, grid=grid, anchors=anchors,
                          anchor_tolerance_m=anchor_tolerance_m)

    @classmethod
    def align_objects(cls, db: Session, scene_id: UUID, user_id: UUID,
                      object_ids, mode: str, distribute_axis: Optional[str] = None):
        _, objects, _, matrices, _ = cls._load_scene(db, scene_id, user_id)
        by_id = {row.id: row for row in objects}
        if len(set(object_ids)) != len(object_ids) or any(object_id not in by_id for object_id in object_ids):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        geometries = {str(object_id): _object_item(by_id[object_id], matrices.get(object_id)).footprint
                      for object_id in object_ids}
        if distribute_axis is not None:
            positions = distribute_equal_spacing(geometries, distribute_axis)
        else:
            positions = align(geometries, mode)
        return {"positions": [{"object_id": UUID(object_id), "x_m": point.x, "y_m": point.y}
                              for object_id, point in positions.items()],
                "persisted": False}
