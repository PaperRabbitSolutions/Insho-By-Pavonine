"""Typed values shared by the pure spatial engine."""

from dataclasses import dataclass, field
import math
from typing import Tuple


class GeometryError(ValueError):
    """Raised when input geometry is invalid or unsupported by an operation."""


class UnsupportedGeometryError(GeometryError):
    """Raised when a canonical geometry kind has no implementation."""


def _finite(*values):
    if not all(math.isfinite(float(value)) for value in values):
        raise GeometryError("Coordinates and measurements must be finite")


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float

    def __post_init__(self):
        _finite(self.x, self.y)


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float

    def __post_init__(self):
        _finite(self.x, self.y, self.z)


@dataclass(frozen=True)
class Transform3:
    position: Vec3 = field(default_factory=lambda: Vec3(0.0, 0.0, 0.0))
    rotation_deg: Vec3 = field(default_factory=lambda: Vec3(0.0, 0.0, 0.0))
    scale: Vec3 = field(default_factory=lambda: Vec3(1.0, 1.0, 1.0))

    def __post_init__(self):
        if min(self.scale.x, self.scale.y, self.scale.z) <= 0:
            raise GeometryError("Transform scales must be positive")


Matrix4 = Tuple[Tuple[float, float, float, float], ...]
