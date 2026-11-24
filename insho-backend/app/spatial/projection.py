"""Ground-plane geometry shared by the editor and synchronized scene compiler."""

import math
from typing import Optional

from app.spatial.geometry2d import (
    Circle2, Geometry2, Polygon2, Rectangle2, Segment2, convex_hull, polygon_is_convex, rectangle_vertices,
)
from app.spatial.math3d import basis_vectors, transform_point
from app.spatial.tolerance import DEFAULT_TOLERANCE
from app.spatial.types import GeometryError, Matrix4, UnsupportedGeometryError, Vec2, Vec3


def _xy(point: Vec3) -> Vec2:
    return Vec2(point.x, point.y)


def _project_points(vertices, world, z=0):
    return tuple(_xy(transform_point(world, Vec3(point.x, point.y, z))) for point in vertices)


def project_xy(footprint: Geometry2, world: Matrix4, *, height_m: Optional[float] = None) -> Geometry2:
    """Project a local section, or the complete centered solid when height is supplied."""
    if isinstance(footprint, Segment2):
        start, end = _project_points((footprint.start, footprint.end), world)
        return Segment2(start, end)
    if isinstance(footprint, Vec2):
        return _project_points((footprint,), world)[0]

    x, y, z = basis_vectors(world)
    eps = DEFAULT_TOLERANCE.relative * 10
    x_len, y_len = math.hypot(x.x, x.y), math.hypot(y.x, y.y)
    orthogonal = abs(x.x * y.x + x.y * y.y) <= eps * x_len * y_len
    vertical_displacement = math.hypot(z.x, z.y) * height_m / 2 if height_m is not None else 0
    spreads = vertical_displacement > DEFAULT_TOLERANCE.linear(vertical_displacement)

    if isinstance(footprint, Circle2):
        if spreads or not orthogonal or not math.isclose(x_len, y_len, rel_tol=eps, abs_tol=DEFAULT_TOLERANCE.relative):
            raise UnsupportedGeometryError("The exact XY projection is outside the supported circle representation.")
        return Circle2(_project_points((footprint.center,), world)[0], footprint.radius * x_len)

    if isinstance(footprint, Rectangle2):
        vertices = rectangle_vertices(footprint)
        if not spreads and not footprint.rotation_deg and orthogonal and min(x_len, y_len) > 1e-15:
            return Rectangle2(_project_points((footprint.center,), world)[0], footprint.width * x_len,
                              footprint.height * y_len, math.degrees(math.atan2(x.y, x.x)))
    else:
        vertices = footprint.vertices
        if spreads and not polygon_is_convex(footprint):
            raise UnsupportedGeometryError("The exact XY outline of a tilted concave prism is unsupported.")

    points = _project_points(vertices, world)
    if spreads:
        points = convex_hull(_project_points(vertices, world, -height_m / 2)
                             + _project_points(vertices, world, height_m / 2))
    determinant = abs(x.x * y.y - x.y * y.x)
    if not spreads and determinant <= eps * x_len * y_len:
        raise UnsupportedGeometryError("The local surface projects edge-on to the XY plane.")
    try:
        return Polygon2(points)
    except GeometryError as exc:
        raise GeometryError(f"The XY projection is degenerate or invalid: {exc}") from exc
