"""Units and deterministic financial arithmetic for production estimates."""

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext
from typing import Literal

from app.services.production_units import convert_weight
from app.spatial_units import convert_area, convert_distance, convert_volume


LengthUnit = Literal["m", "cm", "mm", "ft", "in"]
MaterialUnit = Literal[
    "m", "cm", "mm", "ft", "in", "m2", "cm2", "mm2", "ft2", "in2",
    "m3", "cm3", "mm3", "ft3", "in3", "kg", "g", "lb", "each",
]
CostUnit = Literal[
    "m", "cm", "mm", "ft", "in", "m2", "cm2", "mm2", "ft2", "in2",
    "m3", "cm3", "mm3", "ft3", "in3", "kg", "g", "lb", "each", "h", "day",
]
CostBasis = Literal["estimated", "quoted", "actual"]
CostCategory = Literal["material", "component", "assembly", "fabrication", "labour", "equipment", "finishing", "other"]
FORMULA = "base = round(quantity × rate); discount = round(base × discount_percent / 100); tax = round((base − discount) × tax_percent / 100); total = base − discount + tax"

_LENGTHS = set(LengthUnit.__args__)
_WEIGHT = {"kg", "g", "lb"}
_MAX_QUANTITY = Decimal("999999999999999999.999999")
_MAX_TOTAL = Decimal("9999999999999999999999999999.9999")


def decimal_text(value):
    with localcontext() as context:
        context.prec = 60
        return format((value or Decimal(0)).normalize(), "f")


def convert_quantity(value, original, target):
    if original == target:
        return value
    with localcontext() as context:
        context.prec = 60
        if original in _LENGTHS and target in _LENGTHS:
            return convert_distance(value, original, target)
        if original in _WEIGHT and target in _WEIGHT:
            return convert_weight(value, original, target)
        if original[:-1] in _LENGTHS and target[:-1] in _LENGTHS and original[-1:] == target[-1:]:
            if original.endswith("2"):
                return convert_area(value, original[:-1], target[:-1])
            if original.endswith("3"):
                return convert_volume(value, original[:-1], target[:-1])
    raise ValueError("The quantity and material units are incompatible.")


def compare_quantities(value, unit, required, required_unit):
    if unit == required_unit:
        return value.compare(required)
    if required_unit in _LENGTHS:
        common = "m"
    elif required_unit in _WEIGHT:
        common = "kg"
    elif required_unit[:-1] in _LENGTHS and required_unit[-1:] in {"2", "3"}:
        common = "m" + required_unit[-1]
    else:
        raise ValueError("The quantity and material units are incompatible.")
    return convert_quantity(value, unit, common).compare(convert_quantity(required, required_unit, common))


def required_quantity(base, unit, allowance_percent):
    with localcontext() as context:
        context.prec = 60
        value = base * (1 + allowance_percent / 100)
        if unit == "each":
            value = value.to_integral_value(rounding=ROUND_CEILING)
        value = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if not Decimal(0) < value <= _MAX_QUANTITY:
        raise ValueError("The required quantity exceeds storage range or precision.")
    return value


def money(value, precision):
    with localcontext() as context:
        context.prec = 60
        return value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)


def calculate_cost(quantity, rate, discount_percent, tax_percent, precision):
    with localcontext() as context:
        context.prec = 60
        base = money(quantity * rate, precision)
        discount = money(base * discount_percent / 100, precision)
        tax = money((base - discount) * tax_percent / 100, precision)
        total = base - discount + tax
    if max(base, discount, tax, total) > _MAX_TOTAL:
        raise ValueError("The calculated cost exceeds storage range.")
    return {"base": str(base), "discount": str(discount), "tax": str(tax), "total": str(total), "formula": FORMULA}
