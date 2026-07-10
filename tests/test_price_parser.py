"""Tests for the pure electricity price parser."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.solar_buddy.const import PriceLevel
from custom_components.solar_buddy.price_parser import (
    classify_price,
    interval_at,
    parse_price_data,
    parse_raw_intervals,
)

CPH = ZoneInfo("Europe/Copenhagen")


def hourly_raw(day: datetime, prices: list[float]) -> list[dict]:
    """Build an Energi Data Service style raw list of hourly prices."""
    return [
        {"hour": (day + timedelta(hours=index)).isoformat(), "price": price}
        for index, price in enumerate(prices)
    ]


def test_parse_valid_raw_today() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    raw = hourly_raw(day, [1.0] * 24)
    intervals = parse_raw_intervals(raw, CPH)
    assert len(intervals) == 24
    assert intervals[0].start == day
    assert intervals[0].end == day + timedelta(hours=1)
    assert intervals[-1].end == day + timedelta(hours=24)


def test_parse_quarter_hour_prices() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    raw = [
        {"hour": (day + timedelta(minutes=15 * index)).isoformat(), "price": 1.0}
        for index in range(96)
    ]
    intervals = parse_raw_intervals(raw, CPH)
    assert len(intervals) == 96
    assert intervals[0].end - intervals[0].start == timedelta(minutes=15)
    assert intervals[-1].end - intervals[-1].start == timedelta(minutes=15)


def test_dst_spring_day_has_23_hours() -> None:
    """2026-03-29 in Copenhagen has 23 hours."""
    start = datetime(2026, 3, 29, tzinfo=CPH)
    moments = []
    cursor = start.astimezone(UTC)
    end = (start + timedelta(days=1)).astimezone(UTC)
    while cursor < end:
        moments.append(cursor.astimezone(CPH))
        cursor += timedelta(hours=1)
    assert len(moments) == 23
    raw = [{"hour": moment.isoformat(), "price": 1.0} for moment in moments]
    intervals = parse_raw_intervals(raw, CPH)
    assert len(intervals) == 23
    assert all(i.end - i.start == timedelta(hours=1) for i in intervals)


def test_dst_fall_day_has_25_hours() -> None:
    """2026-10-25 in Copenhagen has 25 hours."""
    start = datetime(2026, 10, 25, tzinfo=CPH)
    moments = []
    cursor = start.astimezone(UTC)
    end = (start + timedelta(days=1)).astimezone(UTC)
    while cursor < end:
        moments.append(cursor.astimezone(CPH))
        cursor += timedelta(hours=1)
    assert len(moments) == 25
    raw = [{"hour": moment.isoformat(), "price": 1.0} for moment in moments]
    intervals = parse_raw_intervals(raw, CPH)
    assert len(intervals) == 25


def test_duplicate_timestamps_are_skipped() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    raw = hourly_raw(day, [1.0, 2.0]) + hourly_raw(day, [9.0])
    intervals = parse_raw_intervals(raw, CPH)
    assert len(intervals) == 2
    assert intervals[0].price == 1.0  # first occurrence wins


def test_invalid_items_are_skipped() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    raw = [
        {"hour": day.isoformat(), "price": 1.0},
        {"hour": "not a timestamp", "price": 2.0},
        {"hour": (day + timedelta(hours=1)).isoformat(), "price": "broken"},
        {"hour": (day + timedelta(hours=2)).isoformat()},
        {"price": 3.0},
        "not a dict",
        None,
        {"hour": (day + timedelta(hours=3)).isoformat(), "price": 4.0},
    ]
    intervals = parse_raw_intervals(raw, CPH)
    assert [interval.price for interval in intervals] == [1.0, 4.0]


def test_non_list_raw_yields_empty() -> None:
    assert parse_raw_intervals(None, CPH) == []
    assert parse_raw_intervals("nonsense", CPH) == []
    assert parse_raw_intervals({}, CPH) == []
    assert parse_raw_intervals([], CPH) == []


def test_naive_timestamps_get_default_timezone() -> None:
    raw = [{"hour": "2026-07-10T00:00:00", "price": 1.0}]
    intervals = parse_raw_intervals(raw, CPH)
    assert intervals[0].start.tzinfo is not None
    assert intervals[0].start.utcoffset() == timedelta(hours=2)


def test_zulu_timestamps_are_supported() -> None:
    raw = [{"hour": "2026-07-10T00:00:00Z", "price": 1.0}]
    intervals = parse_raw_intervals(raw, CPH)
    assert intervals[0].start == datetime(2026, 7, 10, tzinfo=UTC)


def test_parse_price_data_full() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    attributes = {
        "current_price": 1.23,
        "currency": "DKK",
        "unit": "kWh",
        "raw_today": hourly_raw(day, [1.0] * 24),
        "raw_tomorrow": hourly_raw(day + timedelta(days=1), [2.0] * 24),
        "tomorrow_valid": True,
    }
    data = parse_price_data("1.23", attributes, CPH)
    assert data.current_price == 1.23
    assert data.currency == "DKK"
    assert data.unit == "kWh"
    assert len(data.intervals) == 48
    # Today's last interval must end where tomorrow starts.
    assert data.intervals[23].end == data.intervals[24].start


def test_tomorrow_ignored_when_not_valid() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    attributes = {
        "raw_today": hourly_raw(day, [1.0] * 24),
        "raw_tomorrow": hourly_raw(day + timedelta(days=1), [2.0] * 24),
        "tomorrow_valid": False,
    }
    data = parse_price_data("1.0", attributes, CPH)
    assert len(data.intervals) == 24


def test_missing_attributes() -> None:
    data = parse_price_data("2.5", None, CPH)
    assert data.current_price == 2.5
    assert data.intervals == []
    assert data.currency is None
    assert data.unit is None


def test_state_fallback_for_current_price() -> None:
    data = parse_price_data("0.42", {}, CPH)
    assert data.current_price == 0.42
    data = parse_price_data("unknown", {}, CPH)
    assert data.current_price is None


def test_interval_at() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    intervals = parse_raw_intervals(hourly_raw(day, [1.0, 2.0, 3.0, 4.0]), CPH)
    assert interval_at(intervals, day + timedelta(minutes=30)).price == 1.0
    assert interval_at(intervals, day + timedelta(hours=3, minutes=59)).price == 4.0
    assert interval_at(intervals, day + timedelta(hours=4)) is None
    assert interval_at(intervals, day - timedelta(hours=1)) is None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def spread_intervals() -> list:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    return parse_raw_intervals(hourly_raw(day, [float(i) for i in range(24)]), CPH)


def test_classify_negative_price_is_very_cheap() -> None:
    assert classify_price(-0.1, [], 25, 75) is PriceLevel.VERY_CHEAP
    assert classify_price(-5.0, spread_intervals(), 25, 75) is PriceLevel.VERY_CHEAP


def test_classify_unknown_without_price_or_data() -> None:
    assert classify_price(None, spread_intervals(), 25, 75) is PriceLevel.UNKNOWN
    assert classify_price(1.0, [], 25, 75) is PriceLevel.UNKNOWN


def test_classify_levels() -> None:
    intervals = spread_intervals()  # prices 0..23
    assert classify_price(0.5, intervals, 25, 75) is PriceLevel.VERY_CHEAP
    assert classify_price(4.0, intervals, 25, 75) is PriceLevel.CHEAP
    assert classify_price(11.5, intervals, 25, 75) is PriceLevel.NORMAL
    assert classify_price(18.0, intervals, 25, 75) is PriceLevel.EXPENSIVE
    assert classify_price(23.0, intervals, 25, 75) is PriceLevel.VERY_EXPENSIVE


def test_classify_flat_prices_is_normal() -> None:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    intervals = parse_raw_intervals(hourly_raw(day, [1.0] * 24), CPH)
    assert classify_price(1.0, intervals, 25, 75) is PriceLevel.NORMAL


@pytest.mark.parametrize("currency", ["DKK", "EUR", "SEK"])
@pytest.mark.parametrize("unit", ["kWh", "MWh"])
def test_currency_and_unit_are_passed_through(currency: str, unit: str) -> None:
    data = parse_price_data("1.0", {"currency": currency, "unit": unit}, CPH)
    assert data.currency == currency
    assert data.unit == unit
