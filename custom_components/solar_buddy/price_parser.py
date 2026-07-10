"""Parser for electricity price data (Energi Data Service compatible).

Pure module: no Home Assistant imports, fully unit-testable. The parser makes
no assumptions about the number of intervals per day (hourly, 15-minute, and
23/25-hour DST days all work because interval boundaries come from the
timestamps in the data, not from a fixed grid). Currency and unit are passed
through untouched, and the prices are used exactly as the source sensor
reports them (including tariffs and taxes it may have added).
"""

from __future__ import annotations

import itertools
import math
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .const import PriceLevel
from .models import PriceData, PriceInterval
from .normalization import parse_float

# Attribute names used by Energi Data Service, with fallbacks for similar
# price integrations.
_TIMESTAMP_KEYS = ("hour", "start", "time")
_PRICE_KEYS = ("price", "value")

_DEFAULT_INTERVAL = timedelta(hours=1)

# Percentile classification needs a minimum amount of data to be meaningful.
MIN_INTERVALS_FOR_CLASSIFICATION = 4


def _parse_timestamp(raw: Any, default_tz: tzinfo) -> datetime | None:
    """Parse a timestamp that may be a datetime or an ISO string."""
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def _first_present(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def parse_raw_intervals(raw: Any, default_tz: tzinfo) -> list[PriceInterval]:
    """Parse a raw_today/raw_tomorrow style list into sorted intervals.

    Invalid items, duplicate timestamps, and unparsable prices are skipped.
    Interval ends are derived from the next interval's start; the last
    interval gets the most common spacing (defaulting to one hour).
    """
    if not isinstance(raw, (list, tuple)):
        return []

    points: list[tuple[datetime, float]] = []
    seen: set[datetime] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = _parse_timestamp(_first_present(item, _TIMESTAMP_KEYS), default_tz)
        price = parse_float(_first_present(item, _PRICE_KEYS))
        if start is None or price is None or start in seen:
            continue
        seen.add(start)
        points.append((start, price))

    points.sort(key=lambda point: point[0])
    if not points:
        return []

    spacing = _typical_spacing([start for start, _ in points])
    intervals: list[PriceInterval] = []
    for index, (start, price) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else start + spacing
        intervals.append(PriceInterval(start=start, end=end, price=price))
    return intervals


def _typical_spacing(starts: list[datetime]) -> timedelta:
    """Most common spacing between consecutive starts (fallback: one hour)."""
    if len(starts) < 2:
        return _DEFAULT_INTERVAL
    deltas: dict[timedelta, int] = {}
    for earlier, later in itertools.pairwise(starts):
        delta = later - earlier
        if delta > timedelta(0):
            deltas[delta] = deltas.get(delta, 0) + 1
    if not deltas:
        return _DEFAULT_INTERVAL
    return max(deltas, key=lambda delta: deltas[delta])


def parse_price_data(
    state: Any, attributes: dict[str, Any] | None, default_tz: tzinfo
) -> PriceData:
    """Parse an electricity price sensor's state and attributes.

    ``raw_tomorrow`` is only used when ``tomorrow_valid`` is truthy. Missing
    or malformed data results in an empty interval list, never an exception.
    """
    attributes = attributes or {}

    current_price = parse_float(attributes.get("current_price"))
    if current_price is None:
        current_price = parse_float(state)

    intervals = parse_raw_intervals(attributes.get("raw_today"), default_tz)
    if attributes.get("tomorrow_valid"):
        tomorrow = parse_raw_intervals(attributes.get("raw_tomorrow"), default_tz)
        known_starts = {interval.start for interval in intervals}
        intervals.extend(
            interval for interval in tomorrow if interval.start not in known_starts
        )
        intervals.sort(key=lambda interval: interval.start)
        _reflow_interval_ends(intervals)

    currency = attributes.get("currency")
    unit = attributes.get("unit")
    return PriceData(
        currency=str(currency) if currency is not None else None,
        unit=str(unit) if unit is not None else None,
        current_price=current_price,
        intervals=intervals,
    )


def _reflow_interval_ends(intervals: list[PriceInterval]) -> None:
    """Recompute interval ends after merging today and tomorrow."""
    for index, interval in enumerate(intervals[:-1]):
        next_start = intervals[index + 1].start
        if next_start > interval.start:
            interval.end = min(interval.end, next_start) if interval.end else next_start


def interval_at(intervals: list[PriceInterval], when: datetime) -> PriceInterval | None:
    """Return the interval covering ``when``, if any."""
    for interval in intervals:
        if interval.start <= when < interval.end:
            return interval
    return None


def _percentile(sorted_values: list[float], percentile: float) -> float:
    """Linear-interpolated percentile of an ascending-sorted list."""
    if not sorted_values:
        raise ValueError("percentile of empty list")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (percentile / 100.0) * (len(sorted_values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    fraction = rank - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def classify_price(
    price: float | None,
    intervals: list[PriceInterval],
    cheap_percentile: float,
    expensive_percentile: float,
) -> PriceLevel:
    """Classify a price relative to the known intervals.

    Uses relative percentiles instead of hardcoded thresholds because
    currency, taxes and price level vary. Negative prices are always
    ``very_cheap``. With too little data the result is ``unknown``.
    """
    if price is None:
        return PriceLevel.UNKNOWN
    if price < 0.0:
        return PriceLevel.VERY_CHEAP
    if len(intervals) < MIN_INTERVALS_FOR_CLASSIFICATION:
        return PriceLevel.UNKNOWN

    prices = sorted(interval.price for interval in intervals)
    cheap_cutoff = _percentile(prices, cheap_percentile)
    expensive_cutoff = _percentile(prices, expensive_percentile)
    if math.isclose(cheap_cutoff, expensive_cutoff):
        # Essentially flat prices: no meaningful cheap/expensive bands.
        return PriceLevel.NORMAL

    very_cheap_cutoff = _percentile(prices, cheap_percentile / 2.0)
    very_expensive_cutoff = _percentile(
        prices, expensive_percentile + (100.0 - expensive_percentile) / 2.0
    )

    if price <= very_cheap_cutoff:
        return PriceLevel.VERY_CHEAP
    if price <= cheap_cutoff:
        return PriceLevel.CHEAP
    if price >= very_expensive_cutoff:
        return PriceLevel.VERY_EXPENSIVE
    if price >= expensive_cutoff:
        return PriceLevel.EXPENSIVE
    return PriceLevel.NORMAL
