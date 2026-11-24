"""Renderer-independent geometry conversion from canonical local footprints."""

from dataclasses import dataclass
import math
from typing import Optional, Tuple

from app.spatial.geometry2d import Circle2, Geometry2, Polygon2, Rectangle2, Segment2, polygon_mesh_indices, rectangle_vertices
from app.spatial.geometry3d import AABB3, BOX_SURFACE_TRIANGLES, bounds3, box_corners
from app.spatial.math3d import IDENTITY, transform_point
from app.spatial.types import GeometryError, Matrix4, UnsupportedGeometryError, Vec2, Vec3


CYLINDER_SEGMENTS = 64


@dataclass(frozen=True)
class RuntimeMesh:
    kind: str
    mode: str
    vertices: Tuple[Vec3, ...]
    indices: Tuple[int, ...]
    local_bounds: AABB3
    world_bounds: AABB3


def _ring_volume(points, perimeter, caps, height):
    half = height / 2
    size = len(points)
    vertices = tuple(Vec3(p.x, p.y, z) for z in (-half, half) for p in points)
    faces = [(a, c, b) for a, b, c in caps]
    faces.extend((a + size, b + size, c + size) for a, b, c in caps)
    for a, b in zip(perimeter, perimeter[1:] + perimeter[:1]):
        faces.extend(((a, b, b + size), (a, b + size, a + size)))
    return vertices, tuple(faces)


def _cylinder_bounds(circle: Circle2, height: float, matrix: Matrix4) -> AABB3:
    center = transform_point(matrix, Vec3(circle.center.x, circle.center.y, 0))
    extent = tuple(circle.radius * math.hypot(row[0], row[1]) + height / 2 * abs(row[2]) for row in matrix[:3])
    origin = (center.x, center.y, center.z)
    return AABB3(*(tuple(value - offset for value, offset in zip(origin, extent))
                   + tuple(value + offset for value, offset in zip(origin, extent))))


def _flat_indices(faces):
    return tuple(index for face in faces for index in face)


def compile_geometry(footprint: Geometry2, height: Optional[float], world: Matrix4) -> RuntimeMesh:
    if height is not None and (not math.isfinite(height) or height <= 0):
        raise GeometryError("Geometry height must be finite and positive")
    mode = "triangles"
    if isinstance(footprint, Rectangle2):
        if height is None:
            raise GeometryError("Box geometry requires a height")
        kind = "box"
        if footprint.rotation_deg:
            ring = rectangle_vertices(footprint)
            vertices = tuple(Vec3(ring[i].x, ring[i].y, z) for z in (-height / 2, height / 2) for i in (0, 1, 3, 2))
        else:
            vertices = box_corners(Vec3(footprint.center.x, footprint.center.y, 0), Vec3(footprint.width, footprint.height, height))
        indices = _flat_indices(BOX_SURFACE_TRIANGLES)
    elif isinstance(footprint, Polygon2):
        perimeter, caps = polygon_mesh_indices(footprint)
        if height is None:
            kind = "polygon_surface"
            vertices = tuple(Vec3(p.x, p.y, 0) for p in footprint.vertices)
            indices = _flat_indices(caps)
        else:
            kind = "polygon_prism"
            vertices, faces = _ring_volume(footprint.vertices, perimeter, caps, height)
            indices = _flat_indices(faces)
    elif isinstance(footprint, Circle2):
        if height is None:
            raise GeometryError("Cylinder geometry requires a height")
        angles = tuple(math.tau * n / CYLINDER_SEGMENTS for n in range(CYLINDER_SEGMENTS))
        points = tuple(Vec2(footprint.center.x + footprint.radius * math.cos(a),
                            footprint.center.y + footprint.radius * math.sin(a)) for a in angles)
        perimeter = tuple(range(CYLINDER_SEGMENTS))
        caps = tuple((0, n, n + 1) for n in range(1, CYLINDER_SEGMENTS - 1))
        vertices, faces = _ring_volume(points, perimeter, caps, height)
        return RuntimeMesh("cylinder", mode, vertices, _flat_indices(faces),
                           _cylinder_bounds(footprint, height, IDENTITY), _cylinder_bounds(footprint, height, world))
    elif isinstance(footprint, Segment2):
        kind, mode = "line_segment", "lines"
        if footprint.start == footprint.end:
            raise GeometryError("A line segment needs distinct endpoints")
        vertices = tuple(Vec3(p.x, p.y, 0) for p in (footprint.start, footprint.end))
        indices = (0, 1)
    elif isinstance(footprint, Vec2):
        kind, mode = "point", "points"
        vertices = (Vec3(footprint.x, footprint.y, 0),)
        indices = (0,)
    else:
        raise UnsupportedGeometryError("This footprint has no runtime geometry conversion")
    return RuntimeMesh(kind, mode, vertices, indices, bounds3(vertices),
                       bounds3(tuple(transform_point(world, vertex) for vertex in vertices)))
