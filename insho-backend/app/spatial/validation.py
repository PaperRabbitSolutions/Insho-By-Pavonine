"""Scene boundary, broad-phase overlap, and extensible constraint evaluation."""

from dataclasses import dataclass
import math
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from app.spatial.geometry2d import Geometry2, Rectangle2, contains, intersection, minimum_distance
from app.spatial.geometry3d import (
    AABB3, Intersection3DResult, Parallelepiped3, aabb_intersects,
    clearance, contains_point, distance_between, intersects,
)
from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Vec2, Vec3


@dataclass(frozen=True)
class GeometryItem:
    id: str
    geometry_type: str
    footprint: Geometry2
    volume: Parallelepiped3
    upright: bool = True
    world_bounds: Optional[AABB3] = None

    @property
    def aabb(self):
        return self.world_bounds if self.world_bounds is not None else self.volume.aabb


@dataclass(frozen=True)
class BoundaryResult:
    status: str
    contained: bool
    intersects: bool
    aabb: AABB3
    tolerance_m: float


def scene_boundary(width_m: float, depth_m: float, height_m: float) -> Parallelepiped3:
    if min(width_m, depth_m, height_m) <= 0:
        raise GeometryError("Scene dimensions must be positive")
    center = Vec3(width_m / 2, depth_m / 2, height_m / 2)
    return Parallelepiped3.from_center_dimensions(center, Vec3(width_m, depth_m, height_m))


def evaluate_boundary(item: GeometryItem, boundary: Parallelepiped3,
                      tolerance: Tolerance = DEFAULT_TOLERANCE) -> BoundaryResult:
    if item.upright:
        bounds = boundary.aabb
        planar_boundary = Rectangle2(Vec2(bounds.center.x, bounds.center.y), bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y)
        planar_inside = contains(planar_boundary, item.footprint, tolerance)
        z_inside = _z_contains(bounds, item.aabb, tolerance)
        if planar_inside and z_inside:
            return BoundaryResult("inside", True, True, item.aabb, tolerance.linear_m)
        planar_intersects = intersection(planar_boundary, item.footprint, tolerance).intersects
        z_intersects = not (item.aabb.max_z < bounds.min_z - tolerance.linear(item.aabb.max_z, bounds.min_z)
                            or item.aabb.min_z > bounds.max_z + tolerance.linear(item.aabb.min_z, bounds.max_z))
        overlaps = planar_intersects and z_intersects
        return BoundaryResult("partial" if overlaps else "outside", False, overlaps, item.aabb, tolerance.linear_m)
    if item.geometry_type not in {"box", "rectangle", "rect"}:
        return BoundaryResult("unknown", False, False, item.aabb, tolerance.linear_m)
    contained = all(contains_point(boundary, point, tolerance) for point in item.volume.vertices)
    if contained:
        return BoundaryResult("inside", True, True, item.aabb, tolerance.linear_m)
    overlap = intersects(item.volume, boundary, tolerance).intersects
    return BoundaryResult("partial" if overlap else "outside", False, overlap, item.aabb, tolerance.linear_m)


def _z_contains(outer: AABB3, inner: AABB3, tolerance: Tolerance) -> bool:
    return (inner.min_z >= outer.min_z - tolerance.linear(inner.min_z, outer.min_z)
            and inner.max_z <= outer.max_z + tolerance.linear(inner.max_z, outer.max_z))


def _z_overlap(a: AABB3, b: AABB3, tolerance: Tolerance) -> Tuple[bool, bool, float]:
    depth = min(a.max_z, b.max_z) - max(a.min_z, b.min_z)
    eps = tolerance.linear(a.min_z, a.max_z, b.min_z, b.max_z)
    if depth < -eps:
        return False, False, 0.0
    return True, depth <= eps, max(0.0, depth)


def object_intersection(first: GeometryItem, second: GeometryItem,
                        tolerance: Tolerance = DEFAULT_TOLERANCE) -> dict:
    if first.upright and second.upright:
        z_intersects, z_touching, overlap_depth = _z_overlap(first.aabb, second.aabb, tolerance)
        if not z_intersects:
            return {"intersects": False, "classification": "none", "overlap_area_m2": 0.0,
                    "overlap_volume_m3": 0.0, "contact_points": [], "tolerance_m": tolerance.linear_m,
                    "calculation_method": "2D footprint intersection plus vertical interval"}
        planar = intersection(first.footprint, second.footprint, tolerance)
        if not planar.intersects:
            return {"intersects": False, "classification": "none", "overlap_area_m2": planar.overlap_area_m2,
                    "overlap_volume_m3": 0.0, "contact_points": [], "tolerance_m": tolerance.linear_m,
                    "calculation_method": planar.calculation_method + " plus vertical interval"}
        area = planar.overlap_area_m2
        area_positive = area is not None and area > tolerance.area(math.sqrt(max(area, 0.0)))
        if z_touching or planar.classification == "touching":
            classification = "touching"
        else:
            classification = planar.classification
            if classification == "fully_contained" and not (
                (_z_contains(first.aabb, second.aabb, tolerance) and contains(first.footprint, second.footprint, tolerance))
                or (_z_contains(second.aabb, first.aabb, tolerance) and contains(second.footprint, first.footprint, tolerance))
            ):
                classification = "partial_overlap"
        return {"intersects": True, "classification": classification,
                "overlap_area_m2": area,
                "overlap_volume_m3": area * overlap_depth if area_positive else (0.0 if area is not None else None),
                "contact_points": [{"x_m": point.x, "y_m": point.y} for point in planar.contact_points],
                "tolerance_m": tolerance.linear_m,
                "planar_result": planar,
                "calculation_method": planar.calculation_method + " plus vertical interval"}
    if first.geometry_type not in {"box", "rectangle", "rect"} or second.geometry_type not in {"box", "rectangle", "rect"}:
        return {"intersects": None, "classification": "unknown", "overlap_area_m2": None,
                "overlap_volume_m3": None, "tolerance_m": tolerance.linear_m,
                "calculation_method": "3D intersection for a tilted non-box footprint is unsupported"}
    result = intersects(first.volume, second.volume, tolerance)
    return {"intersects": result.intersects, "classification": result.classification,
            "overlap_area_m2": None, "overlap_volume_m3": result.overlap_volume_m3,
            "tolerance_m": result.tolerance_m,
            "calculation_method": result.calculation_method}


def iter_aabb_pairs(bounds: Sequence[AABB3], tolerance: Tolerance = DEFAULT_TOLERANCE, *, margin_m: float = 0.0):
    """Sweep on X, filtering Y/Z before yielding each unordered candidate once."""
    ordered = sorted(range(len(bounds)), key=lambda index: (bounds[index].min_x, index))
    active = []
    for current in ordered:
        box = bounds[current]
        active = [index for index in active
                  if bounds[index].max_x >= box.min_x - margin_m - tolerance.linear(bounds[index].max_x, box.min_x)]
        for prior in active:
            if aabb_intersects(bounds[prior], box, tolerance, margin_m=margin_m):
                yield min(prior, current), max(prior, current)
        active.append(current)


def broad_phase_pairs(items: Sequence[GeometryItem], tolerance: Tolerance = DEFAULT_TOLERANCE) -> Tuple[Tuple[int, int], ...]:
    return tuple(sorted(iter_aabb_pairs([item.aabb for item in items], tolerance)))


def detect_overlaps(items: Sequence[GeometryItem], tolerance: Tolerance = DEFAULT_TOLERANCE) -> Tuple[dict, ...]:
    findings = []
    for first_index, second_index in broad_phase_pairs(items, tolerance):
        first, second = items[first_index], items[second_index]
        result = object_intersection(first, second, tolerance)
        if result["intersects"] is not False:
            findings.append({"first_id": first.id, "second_id": second.id, **result})
    return tuple(findings)


def evaluate_constraint(constraint: Mapping, *, objects: Mapping[str, GeometryItem],
                        zones: Optional[Mapping[str, Geometry2]] = None,
                        scene_boundary_geometry: Optional[Parallelepiped3] = None,
                        tolerance: Tolerance = DEFAULT_TOLERANCE) -> dict:
    """Evaluate known spatial constraints; unsupported types return unknown."""
    constraint_id = str(constraint.get("id") or "")
    kind = str(constraint.get("type") or "")
    subject_id = str(constraint.get("subject_object_id") or "")
    base = {"constraint_id": constraint_id, "constraint_type": kind,
            "subject_object_id": subject_id, "related_object_id": constraint.get("related_object_id"),
            "status": "unknown", "severity": constraint.get("severity"),
            "calculated_value": None, "expected_condition": None,
            "reason": "Constraint type is unsupported or its required geometry is unavailable.",
            "tolerance_m": (constraint["tolerance_m"] if constraint.get("tolerance_m") is not None else tolerance.linear_m)}
    subject = objects.get(subject_id)
    if subject is None:
        return {**base, "reason": "Subject object is unavailable in this scene."}

    if kind == "object_inside_scene":
        if scene_boundary_geometry is None:
            return {**base, "reason": "Scene boundary is unavailable."}
        result = evaluate_boundary(subject, scene_boundary_geometry, tolerance)
        if result.status == "unknown":
            return {**base, "reason": "Scene containment is unsupported for this geometry and transform."}
        return {**base, "status": "pass" if result.contained else "fail",
                "calculated_value": result.status, "expected_condition": "inside",
                "reason": "Object footprint is {} the scene bounds.".format(result.status)}

    if kind == "object_inside_zone":
        zone_id = str(constraint.get("zone_id") or "")
        zone = (zones or {}).get(zone_id)
        if zone is None:
            return {**base, "reason": "Zone geometry is unavailable or not measurable."}
        passed = contains(zone, subject.footprint, tolerance)
        return {**base, "status": "pass" if passed else "fail",
                "related_object_id": zone_id, "calculated_value": passed,
                "expected_condition": "fully_contained", "reason": "Object footprint {} the zone.".format("is within" if passed else "extends outside")}

    related_id = str(constraint.get("related_object_id") or "")
    related = objects.get(related_id)
    if kind in {"objects_do_not_overlap", "minimum_distance", "aligned"} and related is None:
        return {**base, "reason": "Related object is unavailable in this scene."}

    if kind == "objects_do_not_overlap":
        result = object_intersection(subject, related, tolerance)
        if result["intersects"] is None:
            return {**base, "status": "unknown", "calculated_value": result["classification"],
                    "expected_condition": "none", "reason": result["calculation_method"]}
        passed = not result["intersects"]
        return {**base, "status": "pass" if passed else "fail",
                "calculated_value": result["classification"], "expected_condition": "none",
                "reason": "Objects {}.".format("do not overlap" if passed else "intersect or touch")}

    if kind == "minimum_distance":
        requested = constraint.get("minimum_distance_m")
        if requested is None or requested < 0:
            return {**base, "reason": "A non-negative minimum_distance_m is required."}
        if subject.upright and related.upright:
            planar_distance = minimum_distance(subject.footprint, related.footprint, tolerance)
            dz = max(0.0, max(subject.aabb.min_z - related.aabb.max_z, related.aabb.min_z - subject.aabb.max_z))
            measured = math.hypot(planar_distance, dz)
        elif subject.geometry_type in {"box", "rectangle", "rect"} and related.geometry_type in {"box", "rectangle", "rect"}:
            measured = distance_between(subject.volume, related.volume, tolerance)
        else:
            return {**base, "reason": "3D distance for a tilted non-box footprint is unsupported."}
        passed = measured + tolerance.linear(measured, requested) >= requested
        return {**base, "status": "pass" if passed else "fail", "calculated_value": measured,
                "expected_condition": {"minimum_distance_m": requested},
                "reason": "Measured object distance {} the requested threshold.".format("meets" if passed else "is below")}

    if kind == "aligned":
        axis = constraint.get("axis")
        if axis not in {"x", "y", "z"}:
            return {**base, "reason": "Alignment axis must be x, y, or z."}
        first_center, second_center = subject.volume.center, related.volume.center
        delta = abs(getattr(first_center, axis) - getattr(second_center, axis))
        requested_tolerance = float((constraint["tolerance_m"] if constraint.get("tolerance_m") is not None else tolerance.linear_m))
        passed = delta <= requested_tolerance + tolerance.linear(delta, requested_tolerance)
        return {**base, "status": "pass" if passed else "fail", "calculated_value": delta,
                "expected_condition": {"axis": axis, "tolerance_m": requested_tolerance},
                "reason": "Object centers are {} on {}.".format("aligned" if passed else "not aligned", axis)}

    if kind == "within_range":
        minimum, maximum = constraint.get("minimum"), constraint.get("maximum")
        if minimum is None or maximum is None:
            return {**base, "reason": "Minimum and maximum world coordinates are required."}
        center = subject.volume.center
        def component(value, axis):
            return value.get(axis + "_m", value.get(axis)) if isinstance(value, dict) else getattr(value, axis + "_m", getattr(value, axis, None))
        passed = all(all(component(minimum, axis) <= getattr(point, axis) <= component(maximum, axis)
                          for axis in ("x", "y", "z")) for point in subject.volume.vertices)
        return {**base, "status": "pass" if passed else "fail",
                "calculated_value": {"center": {"x": center.x, "y": center.y, "z": center.z},
                                     "bounds": {"min_x": subject.aabb.min_x, "min_y": subject.aabb.min_y, "min_z": subject.aabb.min_z,
                                                "max_x": subject.aabb.max_x, "max_y": subject.aabb.max_y, "max_z": subject.aabb.max_z}},
                "expected_condition": {"minimum": minimum, "maximum": maximum},
                "reason": "Object bounds are {} the requested range.".format("within" if passed else "outside")}

    return base


def clearance_result(first: GeometryItem, second: GeometryItem, requested_m: float,
                     tolerance: Tolerance = DEFAULT_TOLERANCE) -> dict:
    if requested_m < 0:
        raise GeometryError("Requested clearance must be non-negative")
    if first.upright and second.upright:
        planar = minimum_distance(first.footprint, second.footprint, tolerance)
        dz = max(0.0, max(first.aabb.min_z - second.aabb.max_z, second.aabb.min_z - first.aabb.max_z))
        measured = math.hypot(planar, dz)
        method = "2D footprint distance plus vertical interval"
    elif first.geometry_type in {"box", "rectangle", "rect"} and second.geometry_type in {"box", "rectangle", "rect"}:
        measured = distance_between(first.volume, second.volume, tolerance)
        method = "convex box surface distance"
    else:
        raise GeometryError("3D clearance for a tilted non-box footprint is unsupported")
    return {"first_id": first.id, "second_id": second.id, "distance_m": measured,
            "requested_clearance_m": requested_m,
            "clearance_met": measured + tolerance.linear(measured, requested_m) >= requested_m,
            "tolerance_m": tolerance.linear_m, "calculation_method": method,
            "regulatory_assessment": False}
