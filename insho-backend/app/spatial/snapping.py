"""Grid and anchor snapping in canonical world meters (never screen pixels)."""

from dataclasses import dataclass
import math
from typing import Iterable, Optional, Tuple

from app.spatial.geometry2d import AABB2, Geometry2, _edges, geometry_center, geometry_corners
from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Vec2
from app.spatial.vectors import distance


@dataclass(frozen=True)
class Grid2:
    spacing_m: float
    origin: Vec2 = Vec2(0.0, 0.0)

    def __post_init__(self):
        if not math.isfinite(self.spacing_m) or self.spacing_m <= 0:
            raise GeometryError("Grid spacing must be finite and positive")


@dataclass(frozen=True)
class SnapAnchor:
    target_id: str
    kind: str
    point: Vec2


@dataclass(frozen=True)
class SnapResult:
    snapped: bool
    point: Vec2
    target_id: Optional[str]
    target_kind: Optional[str]
    distance_m: Optional[float]
    tolerance_m: float


def grid_index(value: float, spacing_m: float, origin: float = 0.0) -> int:
    if not all(math.isfinite(item) for item in (value, spacing_m, origin)) or spacing_m <= 0:
        raise GeometryError("Grid coordinates must be finite and spacing must be positive")
    quotient = _grid_steps(value, spacing_m, origin)
    # Half-grid ties move away from the origin, independent of Python's round mode.
    return int(math.floor(quotient + 0.5) if quotient >= 0 else math.ceil(quotient - 0.5))


def nearest_grid_value(value: float, spacing_m: float, origin: float = 0.0) -> float:
    result = origin + grid_index(value, spacing_m, origin) * spacing_m
    if not math.isfinite(result):
        raise GeometryError("Snapped grid coordinate exceeds the supported numeric range")
    return result


def _grid_steps(value: float, spacing_m: float, origin: float) -> float:
    quotient = (value - origin) / spacing_m
    if not math.isfinite(quotient):
        raise GeometryError("Grid index exceeds the supported numeric range")
    return quotient


def nearest_grid_point(point: Vec2, grid: Grid2) -> Vec2:
    return Vec2(nearest_grid_value(point.x, grid.spacing_m, grid.origin.x),
                nearest_grid_value(point.y, grid.spacing_m, grid.origin.y))


def is_grid_aligned(point: Vec2, grid: Grid2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> bool:
    nearest = nearest_grid_point(point, grid)
    return distance(point, nearest) <= tolerance.linear(point.x, point.y, nearest.x, nearest.y)


def visible_grid_points(bounds: AABB2, grid: Grid2, *, max_points: int = 10000) -> Tuple[Vec2, ...]:
    """Generate only points within the supplied view bounds and cap allocations."""
    if max_points < 1:
        raise GeometryError("max_points must be positive")
    min_x = math.ceil(_grid_steps(bounds.min_x, grid.spacing_m, grid.origin.x))
    max_x = math.floor(_grid_steps(bounds.max_x, grid.spacing_m, grid.origin.x))
    min_y = math.ceil(_grid_steps(bounds.min_y, grid.spacing_m, grid.origin.y))
    max_y = math.floor(_grid_steps(bounds.max_y, grid.spacing_m, grid.origin.y))
    count = max(0, max_x - min_x + 1) * max(0, max_y - min_y + 1)
    if count > max_points:
        raise GeometryError("Requested grid viewport exceeds max_points")
    return tuple(Vec2(grid.origin.x + x * grid.spacing_m, grid.origin.y + y * grid.spacing_m)
                 for x in range(min_x, max_x + 1) for y in range(min_y, max_y + 1))


def geometry_anchors(target_id: str, geometry: Geometry2) -> Tuple[SnapAnchor, ...]:
    result = [SnapAnchor(target_id, "center", geometry_center(geometry))]
    for index, point in enumerate(geometry_corners(geometry)):
        result.append(SnapAnchor(target_id, "corner_{}".format(index), point))
    edges = _edges(geometry)
    for index, edge in enumerate(edges):
        result.append(SnapAnchor(target_id, "edge_{}".format(index), Vec2((edge.start.x + edge.end.x) / 2, (edge.start.y + edge.end.y) / 2)))
    return tuple(result)


def snap_to_anchors(point: Vec2, anchors: Iterable[SnapAnchor], tolerance_m: float,
                    tolerance: Tolerance = DEFAULT_TOLERANCE) -> SnapResult:
    if not math.isfinite(tolerance_m) or tolerance_m < 0:
        raise GeometryError("Snap tolerance must be finite and non-negative")
    candidates = []
    for anchor in anchors:
        measured = distance(point, anchor.point)
        if math.isfinite(measured) and measured <= tolerance_m + tolerance.linear(measured, tolerance_m):
            candidates.append((measured, anchor.target_id, anchor.kind, anchor.point))
    if not candidates:
        return SnapResult(False, point, None, None, None, tolerance_m)
    measured, target_id, kind, target = min(candidates, key=lambda item: (item[0], item[1], item[2]))
    return SnapResult(True, target, target_id, kind, measured, tolerance_m)


def snap_point(point: Vec2, *, grid: Optional[Grid2] = None,
               anchors: Iterable[SnapAnchor] = (), anchor_tolerance_m: float = 0.0,
               tolerance: Tolerance = DEFAULT_TOLERANCE) -> SnapResult:
    anchor_result = snap_to_anchors(point, anchors, anchor_tolerance_m, tolerance)
    if anchor_result.snapped:
        return anchor_result
    if grid is not None:
        snapped = nearest_grid_point(point, grid)
        return SnapResult(snapped != point, snapped, "grid" if snapped != point else None,
                          "grid" if snapped != point else None, distance(point, snapped), anchor_tolerance_m)
    return SnapResult(False, point, None, None, None, anchor_tolerance_m)
