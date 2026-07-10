"""Pure value-normalization helpers.

No Home Assistant imports: everything here takes raw state/attribute values
and returns validated, normalized numbers. Invalid input never raises; it
yields ``None`` (or a safe fallback where documented).
"""

from __future__ import annotations

import math
from typing import Any, Final

from .const import BatteryPowerSign

UNKNOWN_STATES: Final = frozenset({"unknown", "unavailable", "none", ""})

_POWER_FACTORS: Final[dict[str, float]] = {
    "w": 1.0,
    "watt": 1.0,
    "kw": 1000.0,
    "kilowatt": 1000.0,
    "mw": 1_000_000.0,
}

# Units that indicate an accumulated energy sensor, which must never be used
# as a power input.
ENERGY_UNITS: Final = frozenset({"wh", "kwh", "mwh", "gwh"})


def parse_float(value: Any) -> float | None:
    """Parse a state or attribute into a finite float.

    Returns ``None`` for unknown/unavailable states, non-numeric strings,
    booleans, NaN and infinities.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in UNKNOWN_STATES:
            return None
        try:
            result = float(stripped)
        except ValueError:
            return None
    else:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def power_to_watts(value: Any, unit: str | None) -> float | None:
    """Convert a power reading to watts.

    A missing unit is treated as watts (documented behavior; many template
    sensors do not set a unit). An unsupported or energy unit yields ``None``.
    """
    number = parse_float(value)
    if number is None:
        return None
    if unit is None:
        return number
    normalized_unit = unit.strip().lower()
    if normalized_unit in ENERGY_UNITS:
        return None
    factor = _POWER_FACTORS.get(normalized_unit)
    if factor is None:
        return None
    return number * factor


def parse_percentage(value: Any) -> float | None:
    """Parse a state into a percentage; values outside 0-100 are invalid."""
    number = parse_float(value)
    if number is None or not 0.0 <= number <= 100.0:
        return None
    return number


def non_negative(value: float | None) -> float:
    """Clamp a possibly-unknown value to a non-negative float."""
    if value is None or value < 0.0:
        return 0.0
    return value


def normalize_signed_battery_power(
    value_w: float | None, sign: BatteryPowerSign
) -> tuple[float, float]:
    """Split a signed battery power reading into (charge_w, discharge_w).

    Both returned values are always >= 0. An unknown reading yields (0, 0).
    """
    if value_w is None:
        return (0.0, 0.0)
    if sign is BatteryPowerSign.POSITIVE_IS_DISCHARGING:
        value_w = -value_w
    if value_w >= 0.0:
        return (value_w, 0.0)
    return (0.0, -value_w)
