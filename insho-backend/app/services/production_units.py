"""Normalize production measurements using the Phase 9 length conversions."""

from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
from typing import Literal

from app.spatial_units import convert_area, convert_volume, to_meters


MeasurementKind = Literal["length", "width", "height", "area", "volume", "count", "weight"]
CANONICAL_UNITS: dict[MeasurementKind, str] = {
    "length": "m", "width": "m", "height": "m", "area": "m2",
    "volume": "m3", "count": "each", "weight": "kg",
}
_KG_PER_UNIT = {"kg": Decimal("1"), "g": Decimal("0.001"), "lb": Decimal("0.45359237")}
_RESOLUTION = Decimal("0.000001")
_MAX_VALUE = Decimal("999999999999999999.999999")


def convert_weight(value: Decimal, original: str, target: str) -> Decimal:
    if original not in _KG_PER_UNIT or target not in _KG_PER_UNIT:
        raise ValueError("Unsupported weight unit.")
    return value * _KG_PER_UNIT[original] / _KG_PER_UNIT[target]


def normalize(kind: MeasurementKind, value: Decimal, unit: str) -> Decimal:
    if not value.is_finite() or value < 0 or (kind != "count" and value.is_zero()):
        raise ValueError("Measurement must be finite and positive, or a non-negative count.")
    is_positive = not value.is_zero()
    try:
        with localcontext() as context:
            context.prec = 40
            if kind in ("length", "width", "height"):
                value = to_meters(value, unit)
            elif kind == "area" and unit.endswith("2"):
                value = convert_area(value, unit[:-1], "m")
            elif kind == "volume" and unit.endswith("3"):
                value = convert_volume(value, unit[:-1], "m")
            elif kind == "weight" and unit in _KG_PER_UNIT:
                value = convert_weight(value, unit, "kg")
            elif kind == "count" and unit == "each":
                if value != value.to_integral_value():
                    raise ValueError("Count must be a whole number.")
            else:
                raise ValueError("Unit is unsupported for this measurement kind.")
            if value > _MAX_VALUE:
                raise ValueError("Measurement exceeds the canonical numeric range.")
            rounded = value.quantize(_RESOLUTION, rounding=ROUND_HALF_UP)
            if is_positive and not rounded:
                raise ValueError("Measurement is below canonical precision.")
            return rounded.copy_abs()
    except DecimalException as exc:
        raise ValueError("Measurement exceeds the canonical numeric range.") from exc
