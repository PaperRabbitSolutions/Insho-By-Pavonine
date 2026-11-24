"""Deterministic conversions for spatial values stored canonically in meters."""

from decimal import Decimal, InvalidOperation


_METERS_PER_UNIT = {
    "m": Decimal("1"),
    "cm": Decimal("0.01"),
    "mm": Decimal("0.001"),
    "ft": Decimal("0.3048"),
    "in": Decimal("0.0254"),
}


def to_meters(value, unit: str) -> Decimal:
    """Convert a displayed length to meters without binary-float rounding."""
    if unit not in _METERS_PER_UNIT:
        raise ValueError("Unsupported spatial unit")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Length must be a finite decimal value") from exc
    if not amount.is_finite():
        raise ValueError("Length must be a finite decimal value")
    return amount * _METERS_PER_UNIT[unit]


def from_meters(value, unit: str) -> Decimal:
    """Convert a canonical meter length for display; never mutate stored values."""
    if unit not in _METERS_PER_UNIT:
        raise ValueError("Unsupported spatial unit")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Length must be a finite decimal value") from exc
    if not amount.is_finite():
        raise ValueError("Length must be a finite decimal value")
    return amount / _METERS_PER_UNIT[unit]


def _amount(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Measurement must be a finite decimal value") from exc
    if not result.is_finite():
        raise ValueError("Measurement must be a finite decimal value")
    return result


def convert_distance(value, from_unit: str, to_unit: str) -> Decimal:
    """Convert a distance between supported units without rounding."""
    return to_meters(value, from_unit) / _unit_factor(to_unit)


def convert_coordinates(values, from_unit: str, to_unit: str) -> tuple:
    """Convert an iterable of coordinate components, preserving dimensionality."""
    factor = _unit_factor(from_unit) / _unit_factor(to_unit)
    return tuple(_amount(value) * factor for value in values)


def convert_dimensions(dimensions, from_unit: str, to_unit: str) -> dict:
    """Convert a mapping of linear dimensions (for example width/depth/height)."""
    factor = _unit_factor(from_unit) / _unit_factor(to_unit)
    return {key: _amount(value) * factor for key, value in dimensions.items()}


def convert_area(value, from_unit: str, to_unit: str) -> Decimal:
    """Convert squared units; the linear conversion factor is squared."""
    factor = _unit_factor(from_unit) / _unit_factor(to_unit)
    return _amount(value) * factor * factor


def convert_volume(value, from_unit: str, to_unit: str) -> Decimal:
    """Convert cubed units; the linear conversion factor is cubed."""
    factor = _unit_factor(from_unit) / _unit_factor(to_unit)
    return _amount(value) * factor * factor * factor


def _invalid_unit(unit: str):
    raise ValueError("Unsupported spatial unit: {}".format(unit))


def _unit_factor(unit: str) -> Decimal:
    if unit not in _METERS_PER_UNIT:
        return _invalid_unit(unit)
    return _METERS_PER_UNIT[unit]
