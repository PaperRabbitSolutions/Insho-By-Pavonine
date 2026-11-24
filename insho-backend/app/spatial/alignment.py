"""Pure alignment and equal-spacing calculations for editor operations."""

from typing import Dict, Mapping

from app.spatial.geometry2d import AABB2, Geometry2, geometry_bounds, geometry_center
from app.spatial.types import GeometryError, Vec2


_ALIGNMENTS = {"left", "right", "center_x", "top", "bottom", "center_y", "center"}


def align(geometries: Mapping[str, Geometry2], mode: str) -> Dict[str, Vec2]:
    if len(geometries) < 2:
        raise GeometryError("Alignment needs at least two geometries")
    if mode not in _ALIGNMENTS:
        raise GeometryError("Unsupported alignment mode")
    bounds = {key: geometry_bounds(geometry) for key, geometry in geometries.items()}
    centers = {key: geometry_center(geometry) for key, geometry in geometries.items()}
    if mode == "left":
        target = min(value.min_x for value in bounds.values())
        positions = {key: Vec2(center.x + (target - bounds[key].min_x), center.y) for key, center in centers.items()}
    elif mode == "right":
        target = max(value.max_x for value in bounds.values())
        positions = {key: Vec2(center.x + (target - bounds[key].max_x), center.y) for key, center in centers.items()}
    elif mode == "center":
        target_x = (min(value.min_x for value in bounds.values()) + max(value.max_x for value in bounds.values())) / 2
        target_y = (min(value.min_y for value in bounds.values()) + max(value.max_y for value in bounds.values())) / 2
        return {key: Vec2(target_x, target_y) for key in geometries}
    elif mode == "center_x":
        target = (min(value.min_x for value in bounds.values()) + max(value.max_x for value in bounds.values())) / 2
        positions = {key: Vec2(target, center.y) for key, center in centers.items()}
    elif mode == "top":
        target = max(value.max_y for value in bounds.values())
        positions = {key: Vec2(center.x, center.y + (target - bounds[key].max_y)) for key, center in centers.items()}
    elif mode == "bottom":
        target = min(value.min_y for value in bounds.values())
        positions = {key: Vec2(center.x, center.y + (target - bounds[key].min_y)) for key, center in centers.items()}
    else:
        target = (min(value.min_y for value in bounds.values()) + max(value.max_y for value in bounds.values())) / 2
        positions = {key: Vec2(center.x, target) for key, center in centers.items()}
    return positions


def distribute_equal_spacing(geometries: Mapping[str, Geometry2], axis: str = "x") -> Dict[str, Vec2]:
    if len(geometries) < 3:
        raise GeometryError("Equal spacing needs at least three geometries")
    if axis not in {"x", "y"}:
        raise GeometryError("Distribution axis must be x or y")
    horizontal = axis == "x"
    centers = {key: geometry_center(geometry) for key, geometry in geometries.items()}
    bounds = {key: geometry_bounds(geometry) for key, geometry in geometries.items()}
    ordered = sorted(geometries, key=lambda key: ((bounds[key].min_x + bounds[key].max_x) / 2 if horizontal else (bounds[key].min_y + bounds[key].max_y) / 2, key))
    first, last = bounds[ordered[0]], bounds[ordered[-1]]
    total = (last.max_x - first.min_x if horizontal else last.max_y - first.min_y)
    occupied = sum(value.width if horizontal else value.height for value in bounds.values())
    gap = (total - occupied) / (len(ordered) - 1)
    if gap < 0:
        raise GeometryError("Objects cannot be equally spaced within the endpoint span")
    result = {}
    cursor = first.min_x if horizontal else first.min_y
    for key in ordered:
        box = bounds[key]
        size = box.width if horizontal else box.height
        prior = centers[key]
        result[key] = (Vec2(prior.x + (cursor - box.min_x), prior.y) if horizontal
                       else Vec2(prior.x, prior.y + (cursor - box.min_y)))
        cursor += size + gap
    return result
