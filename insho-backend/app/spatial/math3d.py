"""4x4 affine transformation utilities in column-vector convention."""

import math
from typing import Sequence

from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Matrix4, Transform3, Vec3
from app.spatial.vectors import cross3, dot, normalize


IDENTITY: Matrix4 = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def _matrix(matrix: Sequence[Sequence[float]]) -> Matrix4:
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        raise GeometryError("A transform matrix must be 4 by 4")
    values = tuple(tuple(float(value) for value in row) for row in matrix)
    if not all(math.isfinite(value) for row in values for value in row):
        raise GeometryError("Transform matrix values must be finite")
    return values


def transform_matrix(transform: Transform3) -> Matrix4:
    """Build T * Rz * Ry * Rx * S; Euler angles are degrees, right-handed."""
    rx, ry, rz = (math.radians(value) for value in (
        transform.rotation_deg.x, transform.rotation_deg.y, transform.rotation_deg.z,
    ))
    sx, sy, sz = transform.scale.x, transform.scale.y, transform.scale.z
    cx, cy, cz = math.cos(rx), math.cos(ry), math.cos(rz)
    sin_x, sin_y, sin_z = math.sin(rx), math.sin(ry), math.sin(rz)
    # Rz * Ry * Rx, with scale applied in local coordinates (rightmost).
    rotation = (
        (cz * cy, cz * sin_y * sin_x - sin_z * cx, cz * sin_y * cx + sin_z * sin_x),
        (sin_z * cy, sin_z * sin_y * sin_x + cz * cx, sin_z * sin_y * cx - cz * sin_x),
        (-sin_y, cy * sin_x, cy * cx),
    )
    return (
        (rotation[0][0] * sx, rotation[0][1] * sy, rotation[0][2] * sz, transform.position.x),
        (rotation[1][0] * sx, rotation[1][1] * sy, rotation[1][2] * sz, transform.position.y),
        (rotation[2][0] * sx, rotation[2][1] * sy, rotation[2][2] * sz, transform.position.z),
        (0.0, 0.0, 0.0, 1.0),
    )


def matrix_multiply(left: Matrix4, right: Matrix4) -> Matrix4:
    a, b = _matrix(left), _matrix(right)
    return _matrix(tuple(
        tuple(sum(a[row][k] * b[k][column] for k in range(4)) for column in range(4))
        for row in range(4)
    ))


def compose_transforms(parent: Matrix4, child: Matrix4) -> Matrix4:
    """Compose as parent-world * child-local; the child acts first on points."""
    return matrix_multiply(parent, child)


def _check_basis(matrix):
    normalized = []
    for axis in range(3):
        components = tuple(matrix[row][axis] for row in range(3))
        length = math.hypot(*components)
        if not math.isfinite(length) or length == 0:
            raise GeometryError("The composed transform has an invalid scale.")
        normalized.append(Vec3(*(value / length for value in components)))
    if dot(cross3(normalized[0], normalized[1]), normalized[2]) <= 0:
        raise GeometryError("The composed transform has a collapsed or inverted basis.")


def resolve_world_matrices(local_matrices, parents, *, report=None):
    """Resolve a forest once, optionally reporting bad branches and keeping the valid ones."""
    def issue(node, code, message):
        if report is None:
            raise GeometryError(message)
        report(node, code, message)

    resolved = {}
    for node in parents:
        chain, positions = [], {}
        current = node
        while current is not None and current not in resolved:
            if current in positions:
                for member in chain[positions[current]:]:
                    issue(member, "parent_cycle", "The transform parent chain contains a cycle.")
                    resolved[member] = None
                break
            if current not in parents:
                issue(chain[-1], "missing_parent", "The transform parent is absent from this scene.")
                resolved[chain[-1]] = None
                break
            positions[current] = len(chain)
            chain.append(current)
            current = parents[current]
        for member in reversed(chain):
            if member in resolved:
                continue
            local, parent = local_matrices.get(member), parents[member]
            resolved[member] = None
            if local is None:
                continue
            if parent is not None and resolved[parent] is None:
                issue(member, "parent_transform_unavailable", "The transform parent has no valid world matrix.")
                continue
            try:
                world = local if parent is None else compose_transforms(resolved[parent], local)
                _check_basis(world)
                resolved[member] = world
            except GeometryError as exc:
                issue(member, "invalid_world_transform", str(exc))
    return {node: matrix for node, matrix in resolved.items() if matrix is not None}


def transform_point(matrix: Matrix4, point: Vec3) -> Vec3:
    m = _matrix(matrix)
    x, y, z = point.x, point.y, point.z
    result = tuple(m[row][0] * x + m[row][1] * y + m[row][2] * z + m[row][3] for row in range(4))
    if abs(result[3]) <= DEFAULT_TOLERANCE.relative:
        raise GeometryError("Point transform produced an invalid homogeneous coordinate")
    return Vec3(result[0] / result[3], result[1] / result[3], result[2] / result[3])


def transform_vector(matrix: Matrix4, vector: Vec3) -> Vec3:
    m = _matrix(matrix)
    x, y, z = vector.x, vector.y, vector.z
    return Vec3(
        m[0][0] * x + m[0][1] * y + m[0][2] * z,
        m[1][0] * x + m[1][1] * y + m[1][2] * z,
        m[2][0] * x + m[2][1] * y + m[2][2] * z,
    )


def inverse_matrix(matrix: Matrix4, tolerance: Tolerance = DEFAULT_TOLERANCE) -> Matrix4:
    """Invert a general 4x4 matrix with pivoting; singular matrices are rejected."""
    source = _matrix(matrix)
    work = [list(source[row]) + [1.0 if row == col else 0.0 for col in range(4)] for row in range(4)]
    for column in range(4):
        pivot_row = max(range(column, 4), key=lambda row: abs(work[row][column]))
        pivot = work[pivot_row][column]
        row_scale = max((abs(value) for value in work[pivot_row][:4]), default=0.0)
        if abs(pivot) <= max(1e-300, tolerance.relative * row_scale):
            raise GeometryError("Transform matrix is singular and cannot be inverted")
        work[column], work[pivot_row] = work[pivot_row], work[column]
        divisor = work[column][column]
        work[column] = [value / divisor for value in work[column]]
        for row in range(4):
            if row == column:
                continue
            factor = work[row][column]
            work[row] = [work[row][idx] - factor * work[column][idx] for idx in range(8)]
    return tuple(tuple(work[row][column] for column in range(4, 8)) for row in range(4))


def invert_transform(transform: Transform3, tolerance: Tolerance = DEFAULT_TOLERANCE) -> Matrix4:
    return inverse_matrix(transform_matrix(transform), tolerance)


def local_to_world(point: Vec3, world_matrix: Matrix4) -> Vec3:
    return transform_point(world_matrix, point)


def world_to_local(point: Vec3, world_matrix: Matrix4, tolerance: Tolerance = DEFAULT_TOLERANCE) -> Vec3:
    return transform_point(inverse_matrix(world_matrix, tolerance), point)


def rotate3(vector: Vec3, rotation_deg: Vec3) -> Vec3:
    transform = Transform3(rotation_deg=rotation_deg)
    return transform_vector(transform_matrix(transform), vector)


def normal_transform(matrix: Matrix4, normal: Vec3, tolerance: Tolerance = DEFAULT_TOLERANCE) -> Vec3:
    inverse = inverse_matrix(matrix, tolerance)
    transformed = Vec3(
        inverse[0][0] * normal.x + inverse[1][0] * normal.y + inverse[2][0] * normal.z,
        inverse[0][1] * normal.x + inverse[1][1] * normal.y + inverse[2][1] * normal.z,
        inverse[0][2] * normal.x + inverse[1][2] * normal.y + inverse[2][2] * normal.z,
    )
    return normalize(transformed, tolerance)


def basis_vectors(matrix: Matrix4) -> tuple:
    m = _matrix(matrix)
    return (
        Vec3(m[0][0], m[1][0], m[2][0]),
        Vec3(m[0][1], m[1][1], m[2][1]),
        Vec3(m[0][2], m[1][2], m[2][2]),
    )


def decompose_transform(matrix: Matrix4) -> Transform3:
    """Recover a canonical positive-scale TRS from an affine matrix; reject shear."""
    matrix = _matrix(matrix)
    columns = [tuple(matrix[i][j] for i in range(3)) for j in range(3)]
    scales = [math.hypot(*column) for column in columns]
    if min(scales) <= 0:
        raise GeometryError("The relative transform has a singular scale.")
    unit = [tuple(value / factor for value in column) for column, factor in zip(columns, scales)]
    if any(abs(sum(a * b for a, b in zip(unit[i], unit[j]))) > 1e-10 for i, j in ((0, 1), (0, 2), (1, 2))):
        raise GeometryError("The relative transform requires shear, which canonical transforms cannot store.")
    x, y, z = unit
    orientation = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
    if sum(a * b for a, b in zip(orientation, z)) < 0:
        raise GeometryError("The relative transform requires a reflected scale.")
    horizontal = math.hypot(x[0], x[1])
    ry = math.atan2(-x[2], horizontal)
    if horizontal > 1e-12:
        rx, rz = math.atan2(y[2], z[2]), math.atan2(x[1], x[0])
    else:
        rx, rz = 0.0, math.atan2(-y[0], y[1])
    return Transform3(
        position=Vec3(*(matrix[i][3] for i in range(3))),
        rotation_deg=Vec3(math.degrees(rx), math.degrees(ry), math.degrees(rz)),
        scale=Vec3(*scales),
    )
