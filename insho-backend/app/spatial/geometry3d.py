"""Convex 3D box geometry, containment, SAT intersection, and distance."""

from dataclasses import dataclass
import itertools
import math
from typing import Optional, Tuple

from app.spatial.math3d import transform_point
from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Matrix4, Vec3
from app.spatial.vectors import cross3, distance as vector_distance, dot, length, subtract


_EDGE_INDICES = tuple((index, index ^ (1 << axis)) for axis in range(3) for index in range(8) if not (index & (1 << axis)))
_FACE_TRIPLES = ((0, 1, 2), (0, 1, 4), (0, 2, 4))


@dataclass(frozen=True)
class AABB3:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def center(self):
        return Vec3((self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2, (self.min_z + self.max_z) / 2)

    @property
    def dimensions(self):
        return Vec3(self.max_x - self.min_x, self.max_y - self.min_y, self.max_z - self.min_z)

    @property
    def volume(self):
        dims = self.dimensions
        return dims.x * dims.y * dims.z

    @property
    def corners(self):
        return tuple(Vec3(x, y, z) for x in (self.min_x, self.max_x)
                     for y in (self.min_y, self.max_y) for z in (self.min_z, self.max_z))


@dataclass(frozen=True)
class Parallelepiped3:
    """A box after an arbitrary affine transform, including parent-induced shear."""

    vertices: Tuple[Vec3, ...]

    def __post_init__(self):
        if len(self.vertices) != 8:
            raise GeometryError("A transformed box needs eight corners")
        if len({(p.x, p.y, p.z) for p in self.vertices}) != 8:
            raise GeometryError("Transformed box has degenerate corners")
        if any(not all(math.isfinite(value) for value in (p.x, p.y, p.z)) for p in self.vertices):
            raise GeometryError("Box coordinates must be finite")

    @classmethod
    def from_center_dimensions(cls, center: Vec3, dimensions: Vec3, matrix: Optional[Matrix4] = None):
        if min(dimensions.x, dimensions.y, dimensions.z) <= 0:
            raise GeometryError("Box dimensions must be positive")
        half = (dimensions.x / 2, dimensions.y / 2, dimensions.z / 2)
        local = tuple(Vec3(center.x + (half[0] if index & 1 else -half[0]),
                           center.y + (half[1] if index & 2 else -half[1]),
                           center.z + (half[2] if index & 4 else -half[2])) for index in range(8))
        return cls(tuple(transform_point(matrix, p) for p in local) if matrix else local)

    @property
    def center(self):
        return Vec3(sum(point.x for point in self.vertices) / 8,
                    sum(point.y for point in self.vertices) / 8,
                    sum(point.z for point in self.vertices) / 8)

    @property
    def edge_vectors(self):
        origin = self.vertices[0]
        return tuple(subtract(self.vertices[index], origin) for index in (1, 2, 4))

    @property
    def edges(self):
        return tuple((self.vertices[a], self.vertices[b]) for a, b in _EDGE_INDICES)

    @property
    def face_normals(self):
        normals = []
        # Three independent parallelogram face normals; opposite faces are parallel.
        for a, b, c in _FACE_TRIPLES:
            normal = cross3(subtract(self.vertices[b], self.vertices[a]), subtract(self.vertices[c], self.vertices[a]))
            if length(normal) > 0:
                normals.append(normal)
        return tuple(normals)

    @property
    def aabb(self):
        return AABB3(min(p.x for p in self.vertices), min(p.y for p in self.vertices), min(p.z for p in self.vertices),
                     max(p.x for p in self.vertices), max(p.y for p in self.vertices), max(p.z for p in self.vertices))

    @property
    def volume(self):
        a, b, c = self.edge_vectors
        return abs(dot(a, cross3(b, c)))


@dataclass(frozen=True)
class Intersection3DResult:
    intersects: bool
    classification: str
    overlap_volume_m3: Optional[float]
    tolerance_m: float
    calculation_method: str


def box_corners(center: Vec3, dimensions: Vec3, matrix: Optional[Matrix4] = None) -> Tuple[Vec3, ...]:
    return Parallelepiped3.from_center_dimensions(center, dimensions, matrix).vertices


def bounds3(points: Tuple[Vec3, ...]) -> AABB3:
    if not points:
        raise GeometryError("Cannot calculate bounds of an empty point collection")
    return AABB3(min(p.x for p in points), min(p.y for p in points), min(p.z for p in points),
                 max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))


def _project(points: Tuple[Vec3, ...], axis: Vec3):
    values = [dot(point, axis) for point in points]
    return min(values), max(values)


def _unique_axes(axes, tolerance):
    result = []
    largest = max((length(axis) for axis in axes), default=0.0)
    threshold = max(1e-30, largest * tolerance.relative)
    for axis in axes:
        magnitude = length(axis)
        if magnitude <= threshold:
            continue
        unit = Vec3(axis.x / magnitude, axis.y / magnitude, axis.z / magnitude)
        if not any(abs(abs(dot(unit, prior)) - 1.0) <= tolerance.relative * 10 for prior in result):
            result.append(unit)
    return result


def intersects(first: Parallelepiped3, second: Parallelepiped3, tolerance: Tolerance = DEFAULT_TOLERANCE) -> Intersection3DResult:
    axes = list(first.face_normals) + list(second.face_normals)
    axes.extend(cross3(edge_a, edge_b) for edge_a in first.edge_vectors for edge_b in second.edge_vectors)
    axes = _unique_axes(axes, tolerance)
    if not axes:
        raise GeometryError("Degenerate box has no separating axes")
    touching = False
    for axis in axes:
        lo_a, hi_a = _project(first.vertices, axis)
        lo_b, hi_b = _project(second.vertices, axis)
        gap = max(lo_b - hi_a, lo_a - hi_b)
        if gap > tolerance.linear(lo_a, hi_a, lo_b, hi_b):
            return Intersection3DResult(False, "none", 0.0, tolerance.linear_m, "15-axis separating axis test")
        if abs(gap) <= tolerance.linear(lo_a, hi_a, lo_b, hi_b):
            touching = True
    overlap_volume = _aabb_overlap_volume(first.aabb, second.aabb) if _axis_aligned(first, tolerance) and _axis_aligned(second, tolerance) else None
    if touching:
        return Intersection3DResult(True, "touching", overlap_volume, tolerance.linear_m, "15-axis separating axis test")
    first_in_second = all(contains_point(second, point, tolerance) for point in first.vertices)
    second_in_first = all(contains_point(first, point, tolerance) for point in second.vertices)
    classification = "fully_contained" if first_in_second or second_in_first else "partial_overlap"
    return Intersection3DResult(True, classification, overlap_volume, tolerance.linear_m, "15-axis separating axis test")


def contains_point(box: Parallelepiped3, point: Vec3, tolerance: Tolerance = DEFAULT_TOLERANCE) -> bool:
    origin = box.vertices[0]
    edges = tuple(subtract(box.vertices[index], origin) for index in (1, 2, 4))
    determinant = dot(edges[0], cross3(edges[1], edges[2]))
    determinant_scale = math.prod(length(edge) for edge in edges)
    if abs(determinant) <= max(1e-300, determinant_scale * tolerance.relative):
        raise GeometryError("Degenerate box cannot contain points")
    relative = subtract(point, origin)
    coordinates = (
        dot(relative, cross3(edges[1], edges[2])) / determinant,
        dot(relative, cross3(edges[2], edges[0])) / determinant,
        dot(relative, cross3(edges[0], edges[1])) / determinant,
    )
    eps = tolerance.relative + tolerance.linear_m / max(length(edge) for edge in edges)
    return all(-eps <= value <= 1 + eps for value in coordinates)


def point_triangle_distance(point: Vec3, a: Vec3, b: Vec3, c: Vec3) -> float:
    """Closest-point distance based on region tests for a triangle."""
    ab, ac, ap = subtract(b, a), subtract(c, a), subtract(point, a)
    d1, d2 = dot(ab, ap), dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return vector_distance(point, a)
    bp = subtract(point, b)
    d3, d4 = dot(ab, bp), dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return vector_distance(point, b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return vector_distance(point, Vec3(a.x + v * ab.x, a.y + v * ab.y, a.z + v * ab.z))
    cp = subtract(point, c)
    d5, d6 = dot(ab, cp), dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return vector_distance(point, c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return vector_distance(point, Vec3(a.x + w * ac.x, a.y + w * ac.y, a.z + w * ac.z))
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        edge = subtract(c, b)
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        closest = Vec3(b.x + w * edge.x, b.y + w * edge.y, b.z + w * edge.z)
        return vector_distance(point, closest)
    denominator = 1 / (va + vb + vc)
    v, w = vb * denominator, vc * denominator
    closest = Vec3(a.x + ab.x * v + ac.x * w, a.y + ab.y * v + ac.y * w, a.z + ab.z * v + ac.z * w)
    return vector_distance(point, closest)


_FACE_TRIANGLES = ((0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5),
                   (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
                   (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3))

# The distance routines are winding independent. Render meshes use outward winding.
BOX_SURFACE_TRIANGLES = tuple((a, c, b) for a, b, c in _FACE_TRIANGLES)


def _segment_distance(p1: Vec3, q1: Vec3, p2: Vec3, q2: Vec3, tolerance: Tolerance) -> float:
    d1, d2, r = subtract(q1, p1), subtract(q2, p2), subtract(p1, p2)
    a, e = dot(d1, d1), dot(d2, d2)
    f = dot(d2, r)
    if a <= tolerance.linear_m ** 2 and e <= tolerance.linear_m ** 2:
        return vector_distance(p1, p2)
    if a <= tolerance.linear_m ** 2:
        s, t = 0.0, max(0.0, min(1.0, f / e))
    else:
        c = dot(d1, r)
        if e <= tolerance.linear_m ** 2:
            t, s = 0.0, max(0.0, min(1.0, -c / a))
        else:
            b = dot(d1, d2)
            denominator = a * e - b * b
            s = max(0.0, min(1.0, (b * f - c * e) / denominator)) if abs(denominator) > tolerance.relative else 0.0
            t = (b * s + f) / e
            if t < 0:
                t, s = 0.0, max(0.0, min(1.0, -c / a))
            elif t > 1:
                t, s = 1.0, max(0.0, min(1.0, (b - c) / a))
    c1 = Vec3(p1.x + s * d1.x, p1.y + s * d1.y, p1.z + s * d1.z)
    c2 = Vec3(p2.x + t * d2.x, p2.y + t * d2.y, p2.z + t * d2.z)
    return vector_distance(c1, c2)


def distance_between(first: Parallelepiped3, second: Parallelepiped3, tolerance: Tolerance = DEFAULT_TOLERANCE) -> float:
    if intersects(first, second, tolerance).intersects:
        return 0.0
    distances = []
    for point in first.vertices:
        for a, b, c in _FACE_TRIANGLES:
            distances.append(point_triangle_distance(point, second.vertices[a], second.vertices[b], second.vertices[c]))
    for point in second.vertices:
        for a, b, c in _FACE_TRIANGLES:
            distances.append(point_triangle_distance(point, first.vertices[a], first.vertices[b], first.vertices[c]))
    edges_a, edges_b = first.edges, second.edges
    distances.extend(_segment_distance(a0, a1, b0, b1, tolerance)
                     for a0, a1 in edges_a for b0, b1 in edges_b)
    return min(distances)


def clearance(first: Parallelepiped3, second: Parallelepiped3, requested_m: float,
              tolerance: Tolerance = DEFAULT_TOLERANCE) -> dict:
    if not math.isfinite(requested_m) or requested_m < 0:
        raise GeometryError("Requested clearance must be finite and non-negative")
    measured = distance_between(first, second, tolerance)
    return {
        "distance_m": measured,
        "requested_clearance_m": requested_m,
        "clearance_met": measured + tolerance.linear(measured, requested_m) >= requested_m,
        "tolerance_m": tolerance.linear_m,
        "calculation_method": "convex box surface distance",
    }


def _aabb_overlap_volume(first: AABB3, second: AABB3):
    dx = max(0.0, min(first.max_x, second.max_x) - max(first.min_x, second.min_x))
    dy = max(0.0, min(first.max_y, second.max_y) - max(first.min_y, second.min_y))
    dz = max(0.0, min(first.max_z, second.max_z) - max(first.min_z, second.min_z))
    return dx * dy * dz


def _axis_aligned(box: Parallelepiped3, tolerance: Tolerance) -> bool:
    edges = box.edge_vectors
    for edge in edges:
        nonzero = sum(abs(value) > tolerance.linear(value) for value in (edge.x, edge.y, edge.z))
        if nonzero != 1:
            return False
    return True


def aabb_intersects(first: AABB3, second: AABB3, tolerance: Tolerance = DEFAULT_TOLERANCE,
                    *, margin_m: float = 0.0) -> bool:
    """Test AABB overlap or conservatively retain neighbors within a search margin."""
    return not (
        first.max_x < second.min_x - margin_m - tolerance.linear(first.max_x, second.min_x)
        or second.max_x < first.min_x - margin_m - tolerance.linear(second.max_x, first.min_x)
        or first.max_y < second.min_y - margin_m - tolerance.linear(first.max_y, second.min_y)
        or second.max_y < first.min_y - margin_m - tolerance.linear(second.max_y, first.min_y)
        or first.max_z < second.min_z - margin_m - tolerance.linear(first.max_z, second.min_z)
        or second.max_z < first.min_z - margin_m - tolerance.linear(second.max_z, first.min_z)
    )
