"""Central numerical comparison policy for spatial calculations."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Tolerance:
    """Linear tolerance in meters plus relative tolerance for large coordinates."""

    linear_m: float = 1e-6
    relative: float = 1e-12

    def __post_init__(self):
        if not math.isfinite(self.linear_m) or self.linear_m <= 0:
            raise ValueError("linear_m tolerance must be finite and positive")
        if not math.isfinite(self.relative) or self.relative < 0:
            raise ValueError("relative tolerance must be finite and non-negative")

    def linear(self, *values: float) -> float:
        scale = max((abs(float(value)) for value in values), default=1.0)
        return self.linear_m + self.relative * scale

    def area(self, *values: float) -> float:
        scale = max((abs(float(value)) for value in values), default=1.0)
        return self.linear_m * self.linear_m + self.relative * scale * scale

    def equal(self, left: float, right: float) -> bool:
        return abs(left - right) <= self.linear(left, right)

    def zero(self, value: float, *scale_values: float) -> bool:
        return abs(value) <= self.linear(value, *scale_values)


DEFAULT_TOLERANCE = Tolerance()
