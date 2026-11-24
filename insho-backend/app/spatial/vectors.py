"""Pure vector arithmetic with finite-value and zero-division checks."""

import math

from app.spatial.types import GeometryError, Vec2, Vec3
from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance


def add(a, b):
    if isinstance(a, Vec2) and isinstance(b, Vec2):
        return Vec2(a.x + b.x, a.y + b.y)
    if isinstance(a, Vec3) and isinstance(b, Vec3):
        return Vec3(a.x + b.x, a.y + b.y, a.z + b.z)
    raise GeometryError("Vector dimensions must match")


def subtract(a, b):
    if isinstance(a, Vec2) and isinstance(b, Vec2):
        return Vec2(a.x - b.x, a.y - b.y)
    if isinstance(a, Vec3) and isinstance(b, Vec3):
        return Vec3(a.x - b.x, a.y - b.y, a.z - b.z)
    raise GeometryError("Vector dimensions must match")


def multiply(vector, scalar: float):
    if not math.isfinite(float(scalar)):
        raise GeometryError("Scalar must be finite")
    if isinstance(vector, Vec2):
        return Vec2(vector.x * scalar, vector.y * scalar)
    if isinstance(vector, Vec3):
        return Vec3(vector.x * scalar, vector.y * scalar, vector.z * scalar)
    raise GeometryError("Unsupported vector type")


def divide(vector, scalar: float, tolerance: Tolerance = DEFAULT_TOLERANCE):
    if not math.isfinite(float(scalar)):
        raise GeometryError("Divisor must be finite")
    if tolerance.zero(scalar):
        raise GeometryError("Cannot divide a vector by zero")
    return multiply(vector, 1.0 / scalar)


def dot(a, b) -> float:
    if isinstance(a, Vec2) and isinstance(b, Vec2):
        return a.x * b.x + a.y * b.y
    if isinstance(a, Vec3) and isinstance(b, Vec3):
        return a.x * b.x + a.y * b.y + a.z * b.z
    raise GeometryError("Vector dimensions must match")


def cross2(a: Vec2, b: Vec2) -> float:
    return a.x * b.y - a.y * b.x


def cross3(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(
        a.y * b.z - a.z * b.y,
        a.z * b.x - a.x * b.z,
        a.x * b.y - a.y * b.x,
    )


def length(vector) -> float:
    return math.hypot(vector.x, vector.y, vector.z) if isinstance(vector, Vec3) else math.hypot(vector.x, vector.y)


def distance(a, b) -> float:
    return length(subtract(a, b))


def normalize(vector, tolerance: Tolerance = DEFAULT_TOLERANCE):
    magnitude = length(vector)
    if tolerance.zero(magnitude):
        raise GeometryError("A zero-length vector cannot be normalized")
    return divide(vector, magnitude, tolerance)


def perpendicular(vector: Vec2) -> Vec2:
    return Vec2(-vector.y, vector.x)


def angle_between(a, b, *, degrees=False, tolerance: Tolerance = DEFAULT_TOLERANCE) -> float:
    denominator = length(a) * length(b)
    if tolerance.zero(denominator):
        raise GeometryError("Angle is undefined for a zero-length vector")
    cosine = max(-1.0, min(1.0, dot(a, b) / denominator))
    result = math.acos(cosine)
    return math.degrees(result) if degrees else result


def rotate2(vector: Vec2, angle_deg: float, origin: Vec2 = Vec2(0.0, 0.0)) -> Vec2:
    if not math.isfinite(angle_deg):
        raise GeometryError("Rotation angle must be finite")
    radians = math.radians(angle_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    x, y = vector.x - origin.x, vector.y - origin.y
    return Vec2(origin.x + cosine * x - sine * y, origin.y + sine * x + cosine * y)
