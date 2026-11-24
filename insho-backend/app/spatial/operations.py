"""Pure geometry editing operations used by editor-facing calculations."""

from dataclasses import dataclass
from typing import Dict, Mapping

from app.spatial.geometry2d import (
    Geometry2, geometry_area, geometry_bounds, geometry_center, geometry_corners,
    geometry_perimeter, intersection, minimum_distance, resize_geometry,
    rotate_geometry, translate_geometry,
)
from app.spatial.types import Vec2


@dataclass(frozen=True)
class ClearanceResult:
    measured_m: float
    requested_m: float
    met: bool
    tolerance_m: float


def move(geometry: Geometry2, delta: Vec2) -> Geometry2:
    return translate_geometry(geometry, delta)


def rotate(geometry: Geometry2, angle_deg: float, pivot: Vec2 = None) -> Geometry2:
    return rotate_geometry(geometry, angle_deg, pivot)


def resize(geometry: Geometry2, factor_x: float, factor_y: float = None) -> Geometry2:
    return resize_geometry(geometry, factor_x, factor_y)


def duplicate_geometry(geometry: Geometry2, offset: Vec2) -> Geometry2:
    """Return a translated geometry value; a domain layer assigns its new ID."""
    return translate_geometry(geometry, offset)


def measure(geometry: Geometry2) -> dict:
    bounds = geometry_bounds(geometry)
    return {
        "center": geometry_center(geometry),
        "bounds": bounds,
        "corners": geometry_corners(geometry),
        "area_m2": geometry_area(geometry),
        "perimeter_m": geometry_perimeter(geometry),
        "bounding_radius_m": max(
            ((point.x - bounds.center.x) ** 2 + (point.y - bounds.center.y) ** 2) ** 0.5
            for point in geometry_corners(geometry)
        ),
    }


def clearance(first: Geometry2, second: Geometry2, requested_m: float, tolerance) -> ClearanceResult:
    measured = minimum_distance(first, second, tolerance)
    return ClearanceResult(measured, requested_m,
                           measured + tolerance.linear(measured, requested_m) >= requested_m,
                           tolerance.linear_m)


def overlaps(first: Geometry2, second: Geometry2, tolerance):
    return intersection(first, second, tolerance)
