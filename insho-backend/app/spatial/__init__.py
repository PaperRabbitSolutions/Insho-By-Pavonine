"""Pure, deterministic geometry and transform primitives for INSHO."""

from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Vec2, Vec3, Transform3

__all__ = ["DEFAULT_TOLERANCE", "Tolerance", "GeometryError", "Vec2", "Vec3", "Transform3"]
