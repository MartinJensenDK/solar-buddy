"""Tests for the pure normalization helpers."""

from __future__ import annotations

import pytest

from custom_components.solar_buddy.const import BatteryPowerSign
from custom_components.solar_buddy.normalization import (
    non_negative,
    normalize_signed_battery_power,
    parse_float,
    parse_percentage,
    power_to_watts,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1500, 1500.0),
        (1500.5, 1500.5),
        ("1500", 1500.0),
        (" 1500.5 ", 1500.5),
        ("-42", -42.0),
        (0, 0.0),
        ("unknown", None),
        ("unavailable", None),
        ("Unavailable", None),
        ("", None),
        (None, None),
        (True, None),
        (False, None),
        ("not a number", None),
        ("nan", None),
        ("inf", None),
        ("-inf", None),
        (float("nan"), None),
        (float("inf"), None),
        ([1, 2], None),
    ],
)
def test_parse_float(value, expected) -> None:
    assert parse_float(value) == expected


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("1500", "W", 1500.0),
        ("1.5", "kW", 1500.0),
        ("1.5", "KW", 1500.0),
        ("1.5", " kW ", 1500.0),
        ("0.001", "MW", 1000.0),
        ("-2", "kW", -2000.0),
        ("1500", None, 1500.0),  # missing unit is treated as watts
        ("1500", "kWh", None),  # energy sensors are rejected
        ("1500", "Wh", None),
        ("1500", "bananas", None),
        ("unknown", "W", None),
        ("unavailable", "kW", None),
        (None, "W", None),
    ],
)
def test_power_to_watts(value, unit, expected) -> None:
    assert power_to_watts(value, unit) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("55", 55.0),
        (0, 0.0),
        (100, 100.0),
        ("100.0", 100.0),
        ("101", None),
        ("-1", None),
        ("unknown", None),
        (None, None),
        ("nan", None),
    ],
)
def test_parse_percentage(value, expected) -> None:
    assert parse_percentage(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(100.0, 100.0), (0.0, 0.0), (-50.0, 0.0), (None, 0.0)],
)
def test_non_negative(value, expected) -> None:
    assert non_negative(value) == expected


@pytest.mark.parametrize(
    ("value", "sign", "expected"),
    [
        (2000.0, BatteryPowerSign.POSITIVE_IS_CHARGING, (2000.0, 0.0)),
        (-1500.0, BatteryPowerSign.POSITIVE_IS_CHARGING, (0.0, 1500.0)),
        (1500.0, BatteryPowerSign.POSITIVE_IS_DISCHARGING, (0.0, 1500.0)),
        (-2000.0, BatteryPowerSign.POSITIVE_IS_DISCHARGING, (2000.0, 0.0)),
        (0.0, BatteryPowerSign.POSITIVE_IS_CHARGING, (0.0, 0.0)),
        (0.0, BatteryPowerSign.POSITIVE_IS_DISCHARGING, (0.0, 0.0)),
        (None, BatteryPowerSign.POSITIVE_IS_CHARGING, (0.0, 0.0)),
        (None, BatteryPowerSign.POSITIVE_IS_DISCHARGING, (0.0, 0.0)),
    ],
)
def test_normalize_signed_battery_power(value, sign, expected) -> None:
    """Both spec examples and edge cases; results are never negative."""
    charge, discharge = normalize_signed_battery_power(value, sign)
    assert (charge, discharge) == expected
    assert charge >= 0.0
    assert discharge >= 0.0
