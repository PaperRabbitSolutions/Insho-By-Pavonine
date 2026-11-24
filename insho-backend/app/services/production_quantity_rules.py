"""Deterministic formulas over measured Phase 9 source geometry."""

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext

from app.services.production_geometry import AXES
from app.services.production_units import CANONICAL_UNITS, normalize
from app.spatial.geometry2d import Segment2, geometry_area
from app.spatial.math3d import basis_vectors, transform_point
from app.spatial.projection import project_xy
from app.spatial.tolerance import DEFAULT_TOLERANCE
from app.spatial.types import Vec3
from app.spatial.vectors import cross3, dot, length, multiply, subtract
from app.spatial_units import to_meters


ENGINE_VERSION = 1
RULES = {
    "plan_area": ("area", "area(project_xy(canonical_footprint)) or scene_width * scene_depth"),
    "surface_area": ("area", "norm(cross(transformed_edge_a, transformed_edge_b))"),
    "linear": ("length", "norm(transformed_axis_edge) or distance(transformed_segment_endpoints)"),
    "solid_volume": ("volume", "local_footprint_area * local_height * abs(det(linear_transform))"),
    "object_count": ("count", "one unit per placed physical object"),
    "led_surface": ("area", "edge_a * edge_b; pixels_per_axis = edge / pitch; total_pixels = width_px * height_px"),
    "floral_stems": ("count", "one placed arrangement * configured_stems_per_arrangement"),
}
COUNT_METHODS = {"object_count", "floral_stems"}
_AXES = {"x": 0, "y": 1, "z": 2}
_LED_TOLERANCE_M = Decimal("0.000001")
_MAX_PIXELS_PER_AXIS = 2147483647


def variant(rule):
    return getattr(rule, "face", getattr(rule, "axis", ""))


def parameters(rule):
    values = rule.model_dump(mode="json", exclude={"method"})
    with localcontext() as context:
        context.prec = 50
        if "allowance_percent" in values:
            values["allowance_percent"] = format((rule.allowance_percent or Decimal("0")).normalize(), "f")
        if rule.method == "led_surface":
            values["pixel_pitch"]["value"] = format(rule.pixel_pitch.value.normalize(), "f")
    return values


def _edges(inputs, world, face):
    if inputs["kind"] not in {"box", "scene"}:
        raise ValueError("A rectangular box or scene is required for a face calculation.")
    dimensions = inputs["authored_dimensions"]
    basis = basis_vectors(world)
    return [multiply(basis[_AXES[axis]], float(dimensions[AXES[axis]])) for axis in face]


def _linear(inputs, footprint, world, axis):
    if axis == "path":
        if not isinstance(footprint, Segment2):
            raise ValueError("Path length requires a canonical line segment.")
        start, end = (transform_point(world, Vec3(point.x, point.y, 0)) for point in (footprint.start, footprint.end))
        return Decimal(str(length(subtract(end, start))))
    if inputs["kind"] in {"line_segment", "point"}:
        raise ValueError("A line segment requires the path axis and a point has no length.")
    dimensions = inputs["authored_dimensions"] if inputs["kind"] == "scene" else inputs["local_bounds"]
    span = Decimal(str(dimensions[AXES[axis]]))
    return span * Decimal(str(length(basis_vectors(world)[_AXES[axis]])))


def _solid_volume(inputs, footprint, world):
    dimensions = inputs["authored_dimensions"]
    if inputs["kind"] == "scene":
        return Decimal(dimensions["width_m"]) * Decimal(dimensions["depth_m"]) * Decimal(dimensions["height_m"])
    if inputs["kind"] not in {"box", "cylinder", "polygon_prism"} or dimensions is None:
        raise ValueError("Volume requires canonical closed, extruded geometry.")
    x_axis, y_axis, z_axis = basis_vectors(world)
    return (Decimal(str(geometry_area(footprint))) * Decimal(str(dimensions["height_m"]))
            * Decimal(str(abs(dot(x_axis, cross3(y_axis, z_axis))))))


def _led(inputs, world, rule):
    first, second = _edges(inputs, world, rule.face)
    width, height = length(first), length(second)
    if abs(dot(first, second)) > DEFAULT_TOLERANCE.relative * width * height:
        raise ValueError("LED pixel grids require perpendicular edges.")
    width, height = Decimal(str(width)), Decimal(str(height))
    pitch = to_meters(rule.pixel_pitch.value, rule.pixel_pitch.unit)
    counts = []
    provided = () if rule.resolution is None else (rule.resolution.width_px, rule.resolution.height_px)
    for index, dimension in enumerate((width, height)):
        pixels = provided[index] if provided else int((dimension / pitch).to_integral_value(rounding=ROUND_HALF_UP))
        if not 0 < pixels <= _MAX_PIXELS_PER_AXIS or abs(dimension - pitch * pixels) > _LED_TOLERANCE_M:
            raise ValueError("LED pitch and resolution must fit both measured edges within 0.000001 m.")
        counts.append(pixels)
    return width * height, {
        "width_m": str(normalize("width", width, "m")), "height_m": str(normalize("height", height, "m")),
        "pixel_pitch_m": format(pitch.normalize(), "f"),
        "width_px": counts[0], "height_px": counts[1], "total_pixels": counts[0] * counts[1],
    }


def _base_value(rule, target, inputs, footprint, world):
    method = rule.method
    if inputs["state"] in {"missing", "archived"}:
        raise ValueError("The calculation source is missing or archived.")
    if method in COUNT_METHODS:
        if target != "object" or inputs["state"] != "placed":
            raise ValueError("This rule requires a placed physical object.")
        return Decimal(rule.stems_per_arrangement if method == "floral_stems" else inputs["instances"]), {}
    if inputs["state"] != "measured":
        raise ValueError("Measured canonical geometry is required.")
    if method == "led_surface":
        if target != "object":
            raise ValueError("An LED surface requires a placed physical object.")
        return _led(inputs, world, rule)
    dimensions = inputs["authored_dimensions"]
    if method == "plan_area":
        if target == "scene":
            return Decimal(dimensions["width_m"]) * Decimal(dimensions["depth_m"]), {}
        height = None if dimensions is None else dimensions["height_m"]
        return Decimal(str(geometry_area(project_xy(footprint, world, height_m=height)))), {}
    if method == "surface_area":
        if target == "scene":
            first, second = (Decimal(dimensions[AXES[axis]]) for axis in rule.face)
            return first * second, {}
        first, second = _edges(inputs, world, rule.face)
        return Decimal(str(length(cross3(first, second)))), {}
    if method == "linear":
        return _linear(inputs, footprint, world, rule.axis), {}
    return _solid_volume(inputs, footprint, world), {}


def evaluate(rule, target, inputs, footprint, world):
    kind, formula = RULES[rule.method]
    unit = CANONICAL_UNITS[kind]
    allowance = getattr(rule, "allowance_percent", Decimal("0"))
    if rule.method != "led_surface":
        adjustment = "base * (1 + allowance_percent / 100)"
        if kind == "count":
            adjustment = "ceil(" + adjustment + ")"
        formula += "; result = " + adjustment
    try:
        with localcontext() as context:
            context.prec = 50
            base, outputs = _base_value(rule, target, inputs, footprint, world)
            adjusted = base * (1 + allowance / 100)
            if kind == "count":
                adjusted = adjusted.to_integral_value(rounding=ROUND_CEILING)
            value = normalize(kind, adjusted, unit)
            outputs["base_value"] = str(normalize(kind, base, unit))
            return formula, unit, value, outputs, []
    except (ValueError, ArithmeticError) as exc:
        return formula, unit, None, {}, [{"code": "invalid_calculation", "message": str(exc)}]
