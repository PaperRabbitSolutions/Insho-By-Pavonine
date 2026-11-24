"""Deterministic 2D geometry primitives, predicates, distance, and overlap."""

from dataclasses import dataclass
from functools import cached_property
import math
from typing import List, Optional, Sequence, Tuple, Union

from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Vec2
from app.spatial.vectors import cross2, distance as vector_distance, dot, subtract


@dataclass(frozen=True)
class Segment2:
    start: Vec2
    end: Vec2


@dataclass(frozen=True)
class Rectangle2:
    center: Vec2
    width: float
    height: float
    rotation_deg: float = 0.0

    def __post_init__(self):
        _positive(self.width, "Rectangle width")
        _positive(self.height, "Rectangle height")
        _finite(self.rotation_deg)

    @cached_property
    def vertices(self) -> Tuple[Vec2, ...]:
        half_w, half_h = self.width / 2, self.height / 2
        local = ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h))
        angle = math.radians(self.rotation_deg)
        cosine, sine = math.cos(angle), math.sin(angle)
        return tuple(Vec2(
            self.center.x + x * cosine - y * sine,
            self.center.y + x * sine + y * cosine,
        ) for x, y in local)


@dataclass(frozen=True)
class Circle2:
    center: Vec2
    radius: float

    def __post_init__(self):
        _positive(self.radius, "Circle radius")


@dataclass(frozen=True)
class Polygon2:
    vertices: Tuple[Vec2, ...]

    def __post_init__(self):
        validate_polygon(self.vertices)


@dataclass(frozen=True)
class AABB2:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self):
        _finite(self.min_x, self.min_y, self.max_x, self.max_y)
        if self.max_x < self.min_x or self.max_y < self.min_y:
            raise GeometryError("AABB maximum must not be less than minimum")

    @property
    def width(self):
        return self.max_x - self.min_x

    @property
    def height(self):
        return self.max_y - self.min_y

    @property
    def center(self):
        return Vec2((self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2)

    @property
    def area(self):
        return self.width * self.height

    @property
    def corners(self):
        return (
            Vec2(self.min_x, self.min_y), Vec2(self.max_x, self.min_y),
            Vec2(self.max_x, self.max_y), Vec2(self.min_x, self.max_y),
        )


@dataclass(frozen=True)
class OBB2:
    center: Vec2
    half_width: float
    half_height: float
    rotation_deg: float

    @property
    def area(self):
        return 4 * self.half_width * self.half_height

    @property
    def corners(self):
        return rectangle_vertices(Rectangle2(self.center, 2 * self.half_width, 2 * self.half_height, self.rotation_deg))

    @property
    def aabb(self):
        return geometry_bounds(Polygon2(self.corners))


Geometry2 = Union[Vec2, Segment2, Rectangle2, Circle2, Polygon2]


@dataclass(frozen=True)
class IntersectionResult:
    intersects: bool
    classification: str
    overlap_area_m2: Optional[float]
    contact_points: Tuple[Vec2, ...]
    tolerance_m: float
    calculation_method: str


def _finite(*values):
    if not all(math.isfinite(float(value)) for value in values):
        raise GeometryError("Geometry values must be finite")


def _positive(value, label):
    _finite(value)
    if value <= 0:
        raise GeometryError("{} must be positive".format(label))


def _cross(a: Vec2, b: Vec2, c: Vec2) -> float:
    return cross2(subtract(b, a), subtract(c, a))


def _triangle_terms(vertices: Sequence[Vec2]):
    """Use an origin on the polygon so small footprints keep precision far from zero."""
    if len(vertices) < 3:
        return
    origin, previous = vertices[:2]
    x1, y1 = previous.x - origin.x, previous.y - origin.y
    for current in vertices[2:]:
        x2, y2 = current.x - origin.x, current.y - origin.y
        cross = x1 * y2 - x2 * y1
        _finite(cross)
        yield x1 + x2, y1 + y2, cross
        x1, y1 = x2, y2


def _signed_area(vertices: Sequence[Vec2]) -> float:
    try:
        return math.fsum(cross / 2 for _, _, cross in _triangle_terms(vertices))
    except OverflowError as exc:
        raise GeometryError("Polygon area exceeds the supported numeric range") from exc


def _area_epsilon(vertices: Sequence[Vec2], tolerance: Tolerance) -> float:
    width = max(point.x for point in vertices) - min(point.x for point in vertices)
    height = max(point.y for point in vertices) - min(point.y for point in vertices)
    return tolerance.area(width, height)


def validate_polygon(vertices: Sequence[Vec2], tolerance: Tolerance = DEFAULT_TOLERANCE) -> None:
    if len(vertices) < 3:
        raise GeometryError("A polygon needs at least three distinct vertices")
    for point in vertices:
        _finite(point.x, point.y)
    for index, point in enumerate(vertices):
        for other in vertices[index + 1:]:
            if vector_distance(point, other) <= tolerance.linear(point.x, point.y, other.x, other.y):
                raise GeometryError("Polygon contains repeated or near-repeated vertices")
    if abs(_signed_area(vertices)) <= _area_epsilon(vertices, tolerance):
        raise GeometryError("Polygon has zero or negligible area")
    count = len(vertices)
    for first in range(count):
        a, b = vertices[first], vertices[(first + 1) % count]
        for second in range(first + 1, count):
            if second == first or second == (first + 1) % count or first == (second + 1) % count:
                continue
            c, d = vertices[second], vertices[(second + 1) % count]
            if segment_intersections(Segment2(a, b), Segment2(c, d), tolerance):
                raise GeometryError("Polygon edges self-intersect")


def rectangle_vertices(rectangle: Rectangle2) -> Tuple[Vec2, ...]:
    return rectangle.vertices


def polygon_vertices(geometry: Geometry2) -> Tuple[Vec2, ...]:
    if isinstance(geometry, Rectangle2):
        return rectangle_vertices(geometry)
    if isinstance(geometry, Polygon2):
        return geometry.vertices
    raise GeometryError("Geometry does not have polygon vertices")


def geometry_bounds(geometry: Geometry2) -> AABB2:
    if isinstance(geometry, Circle2):
        return AABB2(geometry.center.x - geometry.radius, geometry.center.y - geometry.radius,
                     geometry.center.x + geometry.radius, geometry.center.y + geometry.radius)
    if isinstance(geometry, Vec2):
        return AABB2(geometry.x, geometry.y, geometry.x, geometry.y)
    if isinstance(geometry, Segment2):
        points = (geometry.start, geometry.end)
    else:
        points = polygon_vertices(geometry)
    return AABB2(min(point.x for point in points), min(point.y for point in points),
                 max(point.x for point in points), max(point.y for point in points))


def geometry_center(geometry: Geometry2) -> Vec2:
    if isinstance(geometry, (Vec2, Circle2, Rectangle2)):
        return geometry if isinstance(geometry, Vec2) else geometry.center
    if isinstance(geometry, Segment2):
        return Vec2((geometry.start.x + geometry.end.x) / 2, (geometry.start.y + geometry.end.y) / 2)
    vertices = geometry.vertices
    terms = tuple(_triangle_terms(vertices))
    area = math.fsum(cross / 2 for _, _, cross in terms)
    cx = math.fsum(x * ((cross / 6) / area) for x, _, cross in terms)
    cy = math.fsum(y * ((cross / 6) / area) for _, y, cross in terms)
    return Vec2(vertices[0].x + cx, vertices[0].y + cy)


def geometry_area(geometry: Geometry2) -> float:
    if isinstance(geometry, Circle2):
        return math.pi * geometry.radius * geometry.radius
    if isinstance(geometry, Rectangle2):
        return geometry.width * geometry.height
    if isinstance(geometry, Polygon2):
        return abs(_signed_area(geometry.vertices))
    return 0.0


def geometry_perimeter(geometry: Geometry2) -> float:
    if isinstance(geometry, Circle2):
        return 2 * math.pi * geometry.radius
    if isinstance(geometry, Rectangle2):
        return 2 * (geometry.width + geometry.height)
    if isinstance(geometry, Segment2):
        return vector_distance(geometry.start, geometry.end)
    if isinstance(geometry, Polygon2):
        return sum(vector_distance(geometry.vertices[i], geometry.vertices[(i + 1) % len(geometry.vertices)]) for i in range(len(geometry.vertices)))
    return 0.0


def geometry_corners(geometry: Geometry2) -> Tuple[Vec2, ...]:
    if isinstance(geometry, Vec2):
        return (geometry,)
    if isinstance(geometry, Segment2):
        return (geometry.start, geometry.end)
    if isinstance(geometry, Circle2):
        return (Vec2(geometry.center.x + geometry.radius, geometry.center.y),
                Vec2(geometry.center.x, geometry.center.y + geometry.radius),
                Vec2(geometry.center.x - geometry.radius, geometry.center.y),
                Vec2(geometry.center.x, geometry.center.y - geometry.radius))
    if isinstance(geometry, (Rectangle2, Polygon2)):
        return polygon_vertices(geometry)
    return ()


def oriented_bounds(geometry: Geometry2) -> Optional[OBB2]:
    if isinstance(geometry, Rectangle2):
        return OBB2(geometry.center, geometry.width / 2, geometry.height / 2, geometry.rotation_deg)
    return None


def translate_geometry(geometry: Geometry2, offset: Vec2) -> Geometry2:
    move = lambda point: Vec2(point.x + offset.x, point.y + offset.y)
    if isinstance(geometry, Vec2):
        return move(geometry)
    if isinstance(geometry, Segment2):
        return Segment2(move(geometry.start), move(geometry.end))
    if isinstance(geometry, Circle2):
        return Circle2(move(geometry.center), geometry.radius)
    if isinstance(geometry, Rectangle2):
        return Rectangle2(move(geometry.center), geometry.width, geometry.height, geometry.rotation_deg)
    return Polygon2(tuple(move(point) for point in geometry.vertices))


def rotate_geometry(geometry: Geometry2, angle_deg: float, origin: Optional[Vec2] = None) -> Geometry2:
    from app.spatial.vectors import rotate2
    pivot = origin or geometry_center(geometry)
    if isinstance(geometry, Vec2):
        return rotate2(geometry, angle_deg, pivot)
    if isinstance(geometry, Segment2):
        return Segment2(rotate2(geometry.start, angle_deg, pivot), rotate2(geometry.end, angle_deg, pivot))
    if isinstance(geometry, Circle2):
        return Circle2(rotate2(geometry.center, angle_deg, pivot), geometry.radius)
    if isinstance(geometry, Rectangle2):
        return Rectangle2(rotate2(geometry.center, angle_deg, pivot), geometry.width, geometry.height, geometry.rotation_deg + angle_deg)
    return Polygon2(tuple(rotate2(point, angle_deg, pivot) for point in geometry.vertices))


def resize_geometry(geometry: Geometry2, factor_x: float, factor_y: Optional[float] = None) -> Geometry2:
    fy = factor_x if factor_y is None else factor_y
    _positive(factor_x, "Resize factor X")
    _positive(fy, "Resize factor Y")
    center = geometry_center(geometry)
    def resize(point):
        return Vec2(center.x + (point.x - center.x) * factor_x, center.y + (point.y - center.y) * fy)
    if isinstance(geometry, Vec2):
        return geometry
    if isinstance(geometry, Segment2):
        return Segment2(resize(geometry.start), resize(geometry.end))
    if isinstance(geometry, Circle2):
        if not math.isclose(factor_x, fy, rel_tol=0.0, abs_tol=DEFAULT_TOLERANCE.relative):
            raise GeometryError("Non-uniform resize of a circle creates an unsupported ellipse")
        return Circle2(geometry.center, geometry.radius * factor_x)
    if isinstance(geometry, Rectangle2):
        return Rectangle2(geometry.center, geometry.width * factor_x, geometry.height * fy, geometry.rotation_deg)
    return Polygon2(tuple(resize(point) for point in geometry.vertices))


def _edges(geometry: Geometry2) -> Tuple[Segment2, ...]:
    if isinstance(geometry, Segment2):
        return (geometry,)
    if isinstance(geometry, Rectangle2):
        points = rectangle_vertices(geometry)
    elif isinstance(geometry, Polygon2):
        points = geometry.vertices
    else:
        return ()
    return tuple(Segment2(points[i], points[(i + 1) % len(points)]) for i in range(len(points)))


def point_segment_distance(point: Vec2, segment: Segment2) -> float:
    edge = subtract(segment.end, segment.start)
    denominator = dot(edge, edge)
    if denominator == 0:
        return vector_distance(point, segment.start)
    parameter = max(0.0, min(1.0, dot(subtract(point, segment.start), edge) / denominator))
    closest = Vec2(segment.start.x + parameter * edge.x, segment.start.y + parameter * edge.y)
    return vector_distance(point, closest)


def point_on_segment(point: Vec2, segment: Segment2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> bool:
    return point_segment_distance(point, segment) <= tolerance.linear(point.x, point.y, segment.start.x, segment.start.y, segment.end.x, segment.end.y)


def segment_intersections(first: Segment2, second: Segment2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> Tuple[Vec2, ...]:
    p, q = first.start, second.start
    r, s = subtract(first.end, first.start), subtract(second.end, second.start)
    rxs = cross2(r, s)
    qmp = subtract(q, p)
    qmpxr = cross2(qmp, r)
    eps = tolerance.linear(p.x, p.y, q.x, q.y, first.end.x, first.end.y, second.end.x, second.end.y)
    first_length = max(math.hypot(r.x, r.y), eps)
    second_length = max(math.hypot(s.x, s.y), eps)
    area_eps_r = eps * first_length
    area_eps_s = eps * second_length
    if abs(rxs) <= max(area_eps_r, area_eps_s):
        if abs(qmpxr) > area_eps_r:
            return ()
        r_len2 = dot(r, r)
        if r_len2 <= eps * eps:
            return (p,) if point_on_segment(p, second, tolerance) else ()
        t0 = dot(qmp, r) / r_len2
        t1 = t0 + dot(s, r) / r_len2
        lo, hi = max(0.0, min(t0, t1)), min(1.0, max(t0, t1))
        if hi < lo - eps / max(math.sqrt(r_len2), eps):
            return ()
        start = Vec2(p.x + max(0.0, lo) * r.x, p.y + max(0.0, lo) * r.y)
        end = Vec2(p.x + min(1.0, hi) * r.x, p.y + min(1.0, hi) * r.y)
        if vector_distance(start, end) <= eps:
            return (start,)
        return (start, end)
    t = cross2(qmp, s) / rxs
    u = cross2(qmp, r) / rxs
    t_eps, u_eps = eps / first_length, eps / second_length
    if -t_eps <= t <= 1 + t_eps and -u_eps <= u <= 1 + u_eps:
        t = max(0.0, min(1.0, t))
        return (Vec2(p.x + t * r.x, p.y + t * r.y),)
    return ()


def point_location(point: Vec2, polygon: Union[Polygon2, Rectangle2], tolerance: Tolerance = DEFAULT_TOLERANCE) -> str:
    vertices = polygon_vertices(polygon)
    edges = tuple(Segment2(vertices[i], vertices[(i + 1) % len(vertices)]) for i in range(len(vertices)))
    if any(point_on_segment(point, edge, tolerance) for edge in edges):
        return "boundary"
    inside = False
    for first, second in zip(vertices, vertices[1:] + vertices[:1]):
        if (first.y > point.y) != (second.y > point.y):
            x_intersection = first.x + (point.y - first.y) * (second.x - first.x) / (second.y - first.y)
            if x_intersection > point.x:
                inside = not inside
    return "inside" if inside else "outside"


def contains_point(container: Geometry2, point: Vec2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> bool:
    if isinstance(container, Vec2):
        return vector_distance(container, point) <= tolerance.linear(container.x, container.y, point.x, point.y)
    if isinstance(container, Circle2):
        return vector_distance(container.center, point) <= container.radius + tolerance.linear(container.radius)
    if isinstance(container, Segment2):
        return point_on_segment(point, container, tolerance)
    return point_location(point, container, tolerance) != "outside"


def _circle_segment_intersections(circle: Circle2, segment: Segment2, tolerance: Tolerance) -> Tuple[Vec2, ...]:
    direction = subtract(segment.end, segment.start)
    offset = subtract(segment.start, circle.center)
    a = dot(direction, direction)
    if a <= tolerance.linear_m * tolerance.linear_m:
        return (segment.start,) if tolerance.equal(vector_distance(circle.center, segment.start), circle.radius) else ()
    b = 2 * dot(offset, direction)
    c = dot(offset, offset) - circle.radius * circle.radius
    discriminant = b * b - 4 * a * c
    discr_eps = tolerance.linear(circle.radius) * max(a, 1.0)
    if discriminant < -discr_eps:
        return ()
    root = math.sqrt(max(0.0, discriminant))
    parameters = [(-b - root) / (2 * a), (-b + root) / (2 * a)]
    result = []
    for t in parameters:
        if -tolerance.relative <= t <= 1 + tolerance.relative:
            point = Vec2(segment.start.x + max(0.0, min(1.0, t)) * direction.x,
                         segment.start.y + max(0.0, min(1.0, t)) * direction.y)
            if not any(vector_distance(point, prior) <= tolerance.linear(point.x, point.y) for prior in result):
                result.append(point)
    return tuple(result)


def _circle_polygon_area(circle: Circle2, polygon: Union[Polygon2, Rectangle2]) -> float:
    """Exact circle/simple-polygon intersection area up to floating-point precision."""
    points = polygon_vertices(polygon)
    total = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        a = Vec2(point.x - circle.center.x, point.y - circle.center.y)
        b = Vec2(next_point.x - circle.center.x, next_point.y - circle.center.y)
        edge = subtract(b, a)
        aa = dot(edge, edge)
        parameters = [0.0, 1.0]
        if aa > 0:
            bb = 2 * dot(a, edge)
            cc = dot(a, a) - circle.radius * circle.radius
            discriminant = bb * bb - 4 * aa * cc
            if discriminant > 0:
                root = math.sqrt(discriminant)
                for t in ((-bb - root) / (2 * aa), (-bb + root) / (2 * aa)):
                    if 0 < t < 1:
                        parameters.append(t)
        parameters.sort()
        for lo, hi in zip(parameters, parameters[1:]):
            first = Vec2(a.x + lo * edge.x, a.y + lo * edge.y)
            second = Vec2(a.x + hi * edge.x, a.y + hi * edge.y)
            midpoint = Vec2((first.x + second.x) / 2, (first.y + second.y) / 2)
            if dot(midpoint, midpoint) <= circle.radius * circle.radius:
                total += cross2(first, second) / 2
            else:
                total += circle.radius * circle.radius * math.atan2(cross2(first, second), dot(first, second)) / 2
    return abs(total)


def _circle_circle_area(first: Circle2, second: Circle2, center_distance: float) -> float:
    r1, r2 = first.radius, second.radius
    if center_distance >= r1 + r2:
        return 0.0
    if center_distance <= abs(r1 - r2):
        return math.pi * min(r1, r2) ** 2
    x1 = max(-1.0, min(1.0, (center_distance ** 2 + r1 ** 2 - r2 ** 2) / (2 * center_distance * r1)))
    x2 = max(-1.0, min(1.0, (center_distance ** 2 + r2 ** 2 - r1 ** 2) / (2 * center_distance * r2)))
    radicand = max(0.0, (-center_distance + r1 + r2) * (center_distance + r1 - r2) *
                   (center_distance - r1 + r2) * (center_distance + r1 + r2))
    return r1 * r1 * math.acos(x1) + r2 * r2 * math.acos(x2) - math.sqrt(radicand) / 2


def _convex(vertices: Sequence[Vec2], tolerance: Tolerance) -> bool:
    signs = []
    eps = _area_epsilon(vertices, tolerance)
    for index in range(len(vertices)):
        value = _cross(vertices[index], vertices[(index + 1) % len(vertices)], vertices[(index + 2) % len(vertices)])
        if abs(value) > eps:
            signs.append(value > 0)
    return not signs or all(sign == signs[0] for sign in signs)


def polygon_is_convex(polygon: Polygon2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> bool:
    return _convex(polygon.vertices, tolerance)


def _point_in_triangle(point: Vec2, a: Vec2, b: Vec2, c: Vec2, tolerance: Tolerance) -> bool:
    signs = (_cross(a, b, point), _cross(b, c, point), _cross(c, a, point))
    eps = _area_epsilon((a, b, c, point), tolerance)
    return all(value >= -eps for value in signs) or all(value <= eps for value in signs)


def _triangulate(vertices: Sequence[Vec2], tolerance: Tolerance) -> List[Tuple[Vec2, Vec2, Vec2]]:
    points = list(vertices if _signed_area(vertices) > 0 else reversed(vertices))
    indices = list(range(len(points)))
    triangles = []
    guard = len(points) * len(points)
    while len(indices) > 3 and guard > 0:
        guard -= 1
        ear_found = False
        for offset, current in enumerate(indices):
            previous = indices[offset - 1]
            following = indices[(offset + 1) % len(indices)]
            a, b, c = points[previous], points[current], points[following]
            if _cross(a, b, c) <= _area_epsilon((a, b, c), tolerance):
                continue
            if any(_point_in_triangle(points[index], a, b, c, tolerance)
                   for index in indices if index not in {previous, current, following}):
                continue
            triangles.append((a, b, c))
            del indices[offset]
            ear_found = True
            break
        if not ear_found:
            raise GeometryError("Polygon cannot be triangulated reliably")
    if len(indices) == 3:
        triangles.append(tuple(points[index] for index in indices))
    return triangles


def polygon_mesh_indices(polygon: Polygon2, tolerance: Tolerance = DEFAULT_TOLERANCE):
    """Return a CCW perimeter and CCW triangles as indices into the authored vertices."""
    points = polygon.vertices
    indices = {point: index for index, point in enumerate(points)}
    perimeter = tuple(range(len(points)))
    if _signed_area(points) < 0:
        perimeter = tuple(reversed(perimeter))
    triangles = tuple(tuple(indices[point] for point in triangle) for triangle in _triangulate(points, tolerance))
    return perimeter, triangles


def _clip_convex(subject: Sequence[Vec2], clip: Sequence[Vec2], tolerance: Tolerance) -> List[Vec2]:
    output = list(subject)
    clip_points = clip if _signed_area(clip) > 0 else tuple(reversed(clip))
    for index, edge_start in enumerate(clip_points):
        edge_end = clip_points[(index + 1) % len(clip_points)]
        input_points, output = output, []
        if not input_points:
            break
        clip_direction = subtract(edge_end, edge_start)
        edge_length = math.hypot(clip_direction.x, clip_direction.y)
        eps = tolerance.area(edge_length)
        previous = input_points[-1]
        previous_inside = _cross(edge_start, edge_end, previous) >= -eps
        for current in input_points:
            current_inside = _cross(edge_start, edge_end, current) >= -eps
            if current_inside != previous_inside:
                direction = subtract(current, previous)
                denominator = cross2(direction, clip_direction)
                if abs(denominator) > tolerance.area(math.hypot(direction.x, direction.y), edge_length):
                    parameter = cross2(subtract(edge_start, previous), clip_direction) / denominator
                    output.append(Vec2(previous.x + parameter * direction.x, previous.y + parameter * direction.y))
            if current_inside:
                output.append(current)
            previous, previous_inside = current, current_inside
    cleaned = []
    for point in output:
        if not cleaned or vector_distance(point, cleaned[-1]) > tolerance.linear(point.x, point.y):
            cleaned.append(point)
    if len(cleaned) > 1 and vector_distance(cleaned[0], cleaned[-1]) <= tolerance.linear(cleaned[0].x, cleaned[0].y):
        cleaned.pop()
    return cleaned


def _polygon_intersection_area(first: Union[Rectangle2, Polygon2], second: Union[Rectangle2, Polygon2], tolerance: Tolerance) -> float:
    a, b = polygon_vertices(first), polygon_vertices(second)
    if _convex(a, tolerance) and _convex(b, tolerance):
        clipped = _clip_convex(a, b, tolerance)
        return abs(_signed_area(clipped)) if len(clipped) >= 3 else 0.0
    area = 0.0
    for tri_a in _triangulate(a, tolerance):
        for tri_b in _triangulate(b, tolerance):
            clipped = _clip_convex(tri_a, tri_b, tolerance)
            if len(clipped) >= 3:
                area += abs(_signed_area(clipped))
    return area


def contains(container: Geometry2, item: Geometry2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> bool:
    if isinstance(item, Vec2):
        return contains_point(container, item, tolerance)
    if isinstance(container, Vec2):
        return isinstance(item, Vec2) and contains_point(container, item, tolerance)
    if isinstance(container, Circle2):
        if isinstance(item, Circle2):
            return vector_distance(container.center, item.center) + item.radius <= container.radius + tolerance.linear(container.radius, item.radius)
        points = (item.start, item.end) if isinstance(item, Segment2) else polygon_vertices(item)
        return all(vector_distance(container.center, point) <= container.radius + tolerance.linear(container.radius) for point in points)
    if isinstance(container, (Rectangle2, Polygon2)):
        if isinstance(item, Circle2):
            if point_location(item.center, container, tolerance) == "outside":
                return False
            return all(point_segment_distance(item.center, edge) + tolerance.linear(item.radius) >= item.radius
                       for edge in _edges(container))
        if isinstance(item, Segment2):
            return _segment_inside_polygon(item, container, tolerance)
        if isinstance(item, (Rectangle2, Polygon2)):
            return all(_segment_inside_polygon(edge, container, tolerance) for edge in _edges(item))
    if isinstance(container, Segment2):
        if isinstance(item, Segment2):
            return point_on_segment(item.start, container, tolerance) and point_on_segment(item.end, container, tolerance)
        return False
    if isinstance(container, Segment2) and isinstance(item, Vec2):
        return point_on_segment(item, container, tolerance)
    return False


def _segment_inside_polygon(segment: Segment2, polygon: Union[Rectangle2, Polygon2], tolerance: Tolerance) -> bool:
    if point_location(segment.start, polygon, tolerance) == "outside" or point_location(segment.end, polygon, tolerance) == "outside":
        return False
    direction = subtract(segment.end, segment.start)
    length2 = dot(direction, direction)
    if length2 <= tolerance.linear_m ** 2:
        return True
    parameters = [0.0, 1.0]
    for edge in _edges(polygon):
        for point in segment_intersections(segment, edge, tolerance):
            parameters.append(max(0.0, min(1.0, dot(subtract(point, segment.start), direction) / length2)))
    parameters = sorted(set(parameters))
    for lo, hi in zip(parameters, parameters[1:]):
        mid = (lo + hi) / 2
        point = Vec2(segment.start.x + mid * direction.x, segment.start.y + mid * direction.y)
        if point_location(point, polygon, tolerance) == "outside":
            return False
    return True


def _circle_inside_polygon(circle: Circle2, polygon: Union[Rectangle2, Polygon2], tolerance: Tolerance) -> bool:
    if point_location(circle.center, polygon, tolerance) == "outside":
        return False
    return all(point_segment_distance(circle.center, edge) + tolerance.linear(circle.radius) >= circle.radius
               for edge in _edges(polygon))


def _deduplicate(points: Sequence[Vec2], tolerance: Tolerance) -> Tuple[Vec2, ...]:
    unique = []
    for point in points:
        if not any(vector_distance(point, prior) <= tolerance.linear(point.x, point.y, prior.x, prior.y) for prior in unique):
            unique.append(point)
    return tuple(unique)


def _intersections(first: Geometry2, second: Geometry2, tolerance: Tolerance) -> Tuple[Vec2, ...]:
    if isinstance(first, Vec2):
        return (first,) if contains_point(second, first, tolerance) else ()
    if isinstance(second, Vec2):
        return (second,) if contains_point(first, second, tolerance) else ()
    if isinstance(first, Circle2) and isinstance(second, Circle2):
        d = vector_distance(first.center, second.center)
        if d <= tolerance.linear(d) and tolerance.equal(first.radius, second.radius):
            return ()
        if d > first.radius + second.radius + tolerance.linear(d, first.radius, second.radius):
            return ()
        if d < abs(first.radius - second.radius) - tolerance.linear(d, first.radius, second.radius) or d <= tolerance.linear(d):
            return ()
        along = (first.radius ** 2 - second.radius ** 2 + d ** 2) / (2 * d)
        h2 = first.radius ** 2 - along ** 2
        if h2 < -tolerance.area(first.radius, second.radius):
            return ()
        h = math.sqrt(max(0.0, h2))
        dx = (second.center.x - first.center.x) / d
        dy = (second.center.y - first.center.y) / d
        base = Vec2(first.center.x + along * dx, first.center.y + along * dy)
        offset = Vec2(-dy * h, dx * h)
        return _deduplicate((Vec2(base.x + offset.x, base.y + offset.y), Vec2(base.x - offset.x, base.y - offset.y)), tolerance)
    if isinstance(first, Circle2):
        if isinstance(second, Segment2):
            return _circle_segment_intersections(first, second, tolerance)
        return _deduplicate(tuple(point for edge in _edges(second) for point in _circle_segment_intersections(first, edge, tolerance)), tolerance)
    if isinstance(second, Circle2):
        return _intersections(second, first, tolerance)
    edges_a = _edges(first) if not isinstance(first, Segment2) else (first,)
    edges_b = _edges(second) if not isinstance(second, Segment2) else (second,)
    return _deduplicate(tuple(point for a in edges_a for b in edges_b for point in segment_intersections(a, b, tolerance)), tolerance)


def intersection(first: Geometry2, second: Geometry2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> IntersectionResult:
    points = _intersections(first, second, tolerance)
    area = None
    method = "boundary and containment predicates"
    if isinstance(first, Circle2) and isinstance(second, Circle2):
        center_distance = vector_distance(first.center, second.center)
        area = _circle_circle_area(first, second, center_distance)
        method = "analytic circle-circle"
    elif isinstance(first, Circle2) and isinstance(second, (Rectangle2, Polygon2)):
        area = _circle_polygon_area(first, second)
        method = "analytic circle-polygon edge integration"
    elif isinstance(second, Circle2) and isinstance(first, (Rectangle2, Polygon2)):
        area = _circle_polygon_area(second, first)
        method = "analytic circle-polygon edge integration"
    elif isinstance(first, (Rectangle2, Polygon2)) and isinstance(second, (Rectangle2, Polygon2)):
        area = _polygon_intersection_area(first, second, tolerance)
        method = "convex clipping" if _convex(polygon_vertices(first), tolerance) and _convex(polygon_vertices(second), tolerance) else "ear-clipped polygon intersection"

    contains_ab = contains(first, second, tolerance)
    contains_ba = contains(second, first, tolerance)
    intersects = bool(points) or contains_ab or contains_ba
    if not intersects:
        if isinstance(first, Circle2) and isinstance(second, Circle2):
            d = vector_distance(first.center, second.center)
            intersects = d <= first.radius + second.radius + tolerance.linear(d, first.radius, second.radius)
        elif isinstance(first, Circle2):
            intersects = _circle_intersects_other(first, second, tolerance)
        elif isinstance(second, Circle2):
            intersects = _circle_intersects_other(second, first, tolerance)
        elif isinstance(first, (Rectangle2, Polygon2)) and isinstance(second, (Rectangle2, Polygon2)):
            if area is not None:
                intersects = area > tolerance.area(math.sqrt(max(area, 0.0)))
    if not intersects:
        classification = "none"
    elif contains_ab or contains_ba:
        classification = "fully_contained"
    elif area is not None and area > tolerance.area(math.sqrt(max(area, 0.0))):
        classification = "partial_overlap"
    else:
        classification = "touching"
    return IntersectionResult(intersects, classification, area, points, tolerance.linear_m, method)


def _circle_intersects_other(circle: Circle2, other: Geometry2, tolerance: Tolerance) -> bool:
    if isinstance(other, Vec2):
        return vector_distance(circle.center, other) <= circle.radius + tolerance.linear(circle.radius)
    if isinstance(other, Segment2):
        return point_segment_distance(circle.center, other) <= circle.radius + tolerance.linear(circle.radius)
    if isinstance(other, (Rectangle2, Polygon2)):
        if point_location(circle.center, other, tolerance) != "outside":
            return True
        return any(point_segment_distance(circle.center, edge) <= circle.radius + tolerance.linear(circle.radius) for edge in _edges(other))
    return False


def distance_between(first: Geometry2, second: Geometry2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> float:
    if intersection(first, second, tolerance).intersects:
        return 0.0
    if isinstance(first, Vec2) and isinstance(second, Vec2):
        return vector_distance(first, second)
    if isinstance(first, Circle2) and isinstance(second, Circle2):
        return max(0.0, vector_distance(first.center, second.center) - first.radius - second.radius)
    if isinstance(first, Circle2):
        if isinstance(second, Vec2):
            return max(0.0, vector_distance(first.center, second) - first.radius)
        if isinstance(second, Segment2):
            return max(0.0, point_segment_distance(first.center, second) - first.radius)
        return max(0.0, min(point_segment_distance(first.center, edge) for edge in _edges(second)) - first.radius)
    if isinstance(second, Circle2):
        return distance_between(second, first, tolerance)
    points_a = (first,) if isinstance(first, Vec2) else ((first.start, first.end) if isinstance(first, Segment2) else polygon_vertices(first))
    points_b = (second,) if isinstance(second, Vec2) else ((second.start, second.end) if isinstance(second, Segment2) else polygon_vertices(second))
    edges_a = _edges(first)
    edges_b = _edges(second)
    if not edges_a:
        return min(point_segment_distance(points_a[0], edge) for edge in edges_b)
    if not edges_b:
        return min(point_segment_distance(points_b[0], edge) for edge in edges_a)
    candidates = [point_segment_distance(point, edge) for point in points_a for edge in edges_b]
    candidates.extend(point_segment_distance(point, edge) for point in points_b for edge in edges_a)
    return min(candidates)


def overlap_area(first: Geometry2, second: Geometry2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> Optional[float]:
    return intersection(first, second, tolerance).overlap_area_m2


def minimum_distance(first: Geometry2, second: Geometry2, tolerance: Tolerance = DEFAULT_TOLERANCE) -> float:
    return distance_between(first, second, tolerance)


def convex_hull(points: Sequence[Vec2]) -> Tuple[Vec2, ...]:
    """Return the convex hull in counterclockwise order."""
    ordered = sorted(set(points), key=lambda point: (point.x, point.y))
    def chain(iterable):
        result = []
        for point in iterable:
            while len(result) > 1 and _cross(result[-2], result[-1], point) <= DEFAULT_TOLERANCE.area():
                result.pop()
            result.append(point)
        return result
    return tuple(chain(ordered)[:-1] + chain(reversed(ordered))[:-1])
