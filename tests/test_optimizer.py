"""Tests for the pure optimization engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.solar_buddy.const import (
    PriceLevel,
    Priority,
    Recommendation,
    SolarBuddyStatus,
    Strategy,
)
from custom_components.solar_buddy.models import EnergySnapshot, OptimizationSettings
from custom_components.solar_buddy.optimizer import evaluate, recommended_current
from custom_components.solar_buddy.price_parser import parse_raw_intervals

CPH = ZoneInfo("Europe/Copenhagen")
NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def snapshot(**overrides) -> EnergySnapshot:
    defaults = {
        "timestamp": NOW,
        "solar_power_w": 5000.0,
        "house_consumption_w": 1000.0,
        "battery_charge_power_w": 0.0,
        "battery_discharge_power_w": 0.0,
        "battery_soc": None,
        "ev_soc": None,
        "ev_min_soc": None,
        "ev_connected": True,
        "ev_charging": False,
        "ev_current_a": None,
        "current_price": None,
    }
    defaults.update(overrides)
    return EnergySnapshot(**defaults)


def settings(**overrides) -> OptimizationSettings:
    defaults = {
        "strategy": Strategy.SOLAR_ONLY,
        "priority": Priority.BATTERY_FIRST,
        "ev_configured": True,
        "battery_configured": False,
        "ev_phases": 1,
        "ev_voltage": 230.0,
        "ev_power_reserve_w": 200.0,
    }
    defaults.update(overrides)
    return OptimizationSettings(**defaults)


def price_intervals(prices: list[float]) -> list:
    day = datetime(2026, 7, 10, tzinfo=CPH)
    raw = [
        {"hour": (day + timedelta(hours=index)).isoformat(), "price": price}
        for index, price in enumerate(prices)
    ]
    return parse_raw_intervals(raw, CPH)


# ---------------------------------------------------------------------------
# recommended_current
# ---------------------------------------------------------------------------
def test_recommended_current_floors_to_step() -> None:
    config = settings()
    # 2300 W at 230 V single-phase = exactly 10 A
    assert recommended_current(2300.0, config) == 10.0
    # 2400 W floors down to 10 A
    assert recommended_current(2400.0, config) == 10.0


def test_recommended_current_clamps_to_max() -> None:
    assert recommended_current(50_000.0, settings()) == 16.0


def test_recommended_current_zero_below_minimum() -> None:
    config = settings()  # min 6 A = 1380 W
    assert recommended_current(1379.0, config) == 0.0
    assert recommended_current(1380.0, config) == 6.0
    assert recommended_current(0.0, config) == 0.0


def test_recommended_current_respects_step() -> None:
    config = settings(ev_current_step=2.0)
    # 2500 W / (230 * 2) = 5.43 -> floor = 5 steps of 2 A = 10 A? No:
    # floor(2500 / (230 * 2)) * 2 = floor(5.43) * 2 = 10 A
    assert recommended_current(2500.0, config) == 10.0


def test_recommended_current_three_phases() -> None:
    config = settings(ev_phases=3)
    # 6 A at 3x230 V = 4140 W
    assert recommended_current(4139.0, config) == 0.0
    assert recommended_current(4140.0, config) == 6.0


# ---------------------------------------------------------------------------
# Data quality gates
# ---------------------------------------------------------------------------
def test_data_not_ready() -> None:
    decision = evaluate(snapshot(), [], settings(), data_ready=False)
    assert decision.status is SolarBuddyStatus.WAITING_FOR_DATA
    assert decision.recommendation is Recommendation.DATA_NOT_READY
    assert not decision.should_start_ev
    assert not decision.should_stop_ev
    assert decision.recommended_ev_current_a == 0.0


def test_stale_data() -> None:
    decision = evaluate(snapshot(), [], settings(), data_ready=False, stale=True)
    assert decision.status is SolarBuddyStatus.STALE_DATA
    assert not decision.should_start_ev


# ---------------------------------------------------------------------------
# Core surplus scenarios
# ---------------------------------------------------------------------------
def test_no_surplus() -> None:
    decision = evaluate(
        snapshot(solar_power_w=500.0, house_consumption_w=1500.0),
        [],
        settings(),
        data_ready=True,
    )
    assert decision.solar_surplus_w == -1000.0
    assert decision.recommendation is Recommendation.NO_SURPLUS
    assert not decision.should_start_ev


def test_exactly_enough_for_minimum_current() -> None:
    # 6 A at 230 V = 1380 W + 200 W reserve = 1580 W surplus needed
    decision = evaluate(
        snapshot(solar_power_w=1580.0, house_consumption_w=0.0),
        [],
        settings(),
        data_ready=True,
    )
    assert decision.recommended_ev_current_a == 6.0
    assert decision.should_start_ev
    assert decision.recommendation is Recommendation.EV_CHARGE_RECOMMENDED


def test_large_surplus_clamps_to_max() -> None:
    decision = evaluate(
        snapshot(solar_power_w=20_000.0, house_consumption_w=500.0),
        [],
        settings(),
        data_ready=True,
    )
    assert decision.recommended_ev_current_a == 16.0
    assert decision.should_start_ev


def test_surplus_below_minimum_stops_charging() -> None:
    decision = evaluate(
        snapshot(solar_power_w=1500.0, house_consumption_w=1000.0, ev_charging=True),
        [],
        settings(),
        data_ready=True,
    )
    assert decision.recommendation is Recommendation.SURPLUS_BELOW_MINIMUM
    assert decision.should_stop_ev


def test_no_ev_configured() -> None:
    decision = evaluate(
        snapshot(), [], settings(ev_configured=False), data_ready=True
    )
    assert decision.recommendation is Recommendation.NO_EV_CONFIGURED
    assert decision.solar_surplus_w == 4000.0
    assert not decision.should_start_ev


def test_ev_not_connected() -> None:
    decision = evaluate(
        snapshot(ev_connected=False, ev_charging=True), [], settings(), data_ready=True
    )
    assert decision.recommendation is Recommendation.EV_NOT_CONNECTED
    assert decision.should_stop_ev
    assert decision.recommended_ev_current_a == 0.0


def test_ev_target_reached() -> None:
    decision = evaluate(
        snapshot(ev_soc=85.0, ev_charging=True),
        [],
        settings(ev_target_soc=80.0),
        data_ready=True,
    )
    assert decision.recommendation is Recommendation.EV_TARGET_REACHED
    assert decision.should_stop_ev


# ---------------------------------------------------------------------------
# Priorities
# ---------------------------------------------------------------------------
def test_battery_first_reserves_battery_draw() -> None:
    decision = evaluate(
        snapshot(battery_charge_power_w=2000.0),
        [],
        settings(battery_configured=True, priority=Priority.BATTERY_FIRST),
        data_ready=True,
    )
    # surplus 4000 - battery 2000 - reserve 200 = 1800 W available
    assert decision.available_ev_power_w == 1800.0


def test_ev_first_ignores_battery_draw() -> None:
    decision = evaluate(
        snapshot(battery_charge_power_w=2000.0),
        [],
        settings(battery_configured=True, priority=Priority.EV_FIRST),
        data_ready=True,
    )
    assert decision.available_ev_power_w == 3800.0


def test_balanced_prefers_battery_below_reserve() -> None:
    decision = evaluate(
        snapshot(battery_charge_power_w=2000.0, battery_soc=10.0),
        [],
        settings(
            battery_configured=True,
            priority=Priority.BALANCED,
            battery_reserve_soc=20.0,
        ),
        data_ready=True,
    )
    assert decision.available_ev_power_w == 1800.0


def test_balanced_prefers_ev_below_min_soc() -> None:
    decision = evaluate(
        snapshot(
            battery_charge_power_w=2000.0,
            battery_soc=10.0,
            ev_soc=20.0,
            ev_min_soc=40.0,
        ),
        [],
        settings(battery_configured=True, priority=Priority.BALANCED),
        data_ready=True,
    )
    assert decision.available_ev_power_w == 3800.0


def test_battery_discharge_is_not_free_surplus() -> None:
    """Discharging must never inflate the EV's available power."""
    decision = evaluate(
        snapshot(battery_discharge_power_w=3000.0),
        [],
        settings(battery_configured=True),
        data_ready=True,
    )
    assert decision.available_ev_power_w == 3800.0  # surplus - reserve, no bonus


# ---------------------------------------------------------------------------
# Price behavior
# ---------------------------------------------------------------------------
def test_grid_charge_on_cheap_price_below_min_soc() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(
            solar_power_w=0.0,
            house_consumption_w=500.0,
            ev_soc=20.0,
            ev_min_soc=40.0,
            current_price=1.0,
        ),
        intervals,
        settings(strategy=Strategy.PRICE_AWARE),
        data_ready=True,
    )
    assert decision.recommendation is Recommendation.GRID_CHARGE_CHEAP
    assert decision.recommended_ev_current_a == 16.0
    assert decision.should_start_ev
    assert decision.price_level == PriceLevel.VERY_CHEAP.value


def test_no_grid_charge_in_solar_only() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(
            solar_power_w=0.0,
            house_consumption_w=500.0,
            ev_soc=20.0,
            ev_min_soc=40.0,
            current_price=1.0,
        ),
        intervals,
        settings(strategy=Strategy.SOLAR_ONLY),
        data_ready=True,
    )
    assert decision.recommendation is Recommendation.NO_SURPLUS
    assert not decision.should_start_ev


def test_expensive_price_blocks_discretionary_charging() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(
            solar_power_w=1000.0,
            house_consumption_w=900.0,
            ev_soc=60.0,
            ev_min_soc=40.0,
            ev_charging=True,
            current_price=22.0,
        ),
        intervals,
        settings(strategy=Strategy.PRICE_AWARE),
        data_ready=True,
    )
    assert decision.recommendation is Recommendation.EV_BLOCKED_EXPENSIVE
    assert decision.should_stop_ev


def test_negative_price_is_very_cheap() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(
            solar_power_w=0.0,
            ev_soc=20.0,
            ev_min_soc=40.0,
            current_price=-0.5,
        ),
        intervals,
        settings(strategy=Strategy.BALANCED),
        data_ready=True,
    )
    assert decision.price_level == PriceLevel.VERY_CHEAP.value
    assert decision.recommendation is Recommendation.GRID_CHARGE_CHEAP


def test_missing_price_data_yields_unknown_level() -> None:
    decision = evaluate(snapshot(), [], settings(), data_ready=True)
    assert decision.price_level == PriceLevel.UNKNOWN.value


def test_surplus_charging_still_works_with_expensive_price() -> None:
    """Solar surplus charging is fine even when grid power is expensive."""
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(current_price=22.0, ev_soc=60.0, ev_min_soc=40.0),
        intervals,
        settings(strategy=Strategy.PRICE_AWARE),
        data_ready=True,
    )
    assert decision.recommendation is Recommendation.EV_CHARGE_RECOMMENDED
    assert decision.should_start_ev


# ---------------------------------------------------------------------------
# Status + hysteresis primitives
# ---------------------------------------------------------------------------
def test_monitor_only_still_computes_recommendations() -> None:
    decision = evaluate(
        snapshot(), [], settings(strategy=Strategy.MONITOR_ONLY), data_ready=True
    )
    assert decision.status is SolarBuddyStatus.MONITORING
    assert decision.recommended_ev_current_a > 0.0


def test_status_active_with_automatic_control() -> None:
    decision = evaluate(
        snapshot(),
        [],
        settings(strategy=Strategy.SOLAR_ONLY, automatic_control=True),
        data_ready=True,
    )
    assert decision.status is SolarBuddyStatus.ACTIVE


def test_status_monitoring_when_control_disabled() -> None:
    decision = evaluate(
        snapshot(),
        [],
        settings(strategy=Strategy.SOLAR_ONLY, automatic_control=False),
        data_ready=True,
    )
    assert decision.status is SolarBuddyStatus.MONITORING


def test_current_change_needs_full_step() -> None:
    # Charging at 10 A, recommendation computes to 10 A -> no change
    decision = evaluate(
        snapshot(
            solar_power_w=2500.0,
            house_consumption_w=0.0,
            ev_charging=True,
            ev_current_a=10.0,
        ),
        [],
        settings(),
        data_ready=True,
    )
    assert decision.recommended_ev_current_a == 10.0
    assert not decision.should_change_ev_current
    assert not decision.should_start_ev

    # Same situation but currently at 12 A -> change needed
    decision = evaluate(
        snapshot(
            solar_power_w=2500.0,
            house_consumption_w=0.0,
            ev_charging=True,
            ev_current_a=12.0,
        ),
        [],
        settings(),
        data_ready=True,
    )
    assert decision.should_change_ev_current


def test_identical_decisions_are_stable() -> None:
    """The same input always yields the same decision (deterministic)."""
    first = evaluate(snapshot(), [], settings(), data_ready=True)
    second = evaluate(snapshot(), [], settings(), data_ready=True)
    assert first == second


# ---------------------------------------------------------------------------
# Phase 3: grid charging plan
# ---------------------------------------------------------------------------
from custom_components.solar_buddy.optimizer import (  # noqa: E402
    next_departure,
    plan_grid_windows,
    required_charge_seconds,
)

# NOW is 12:00 UTC = 14:00 in Copenhagen (summer time).
LOCAL_NOW = NOW.astimezone(CPH)


def test_required_charge_seconds() -> None:
    # 40 % of 60 kWh at 90 % efficiency and 11040 W (16 A, 3x230 V)
    seconds = required_charge_seconds(40.0, 80.0, 60.0, 90.0, 11040.0)
    assert seconds == pytest.approx(26666.67 / 11040.0 * 3600.0, rel=1e-3)
    assert required_charge_seconds(80.0, 40.0, 60.0, 90.0, 11040.0) == 0.0
    assert required_charge_seconds(None, 80.0, 60.0, 90.0, 11040.0) is None
    assert required_charge_seconds(40.0, 80.0, None, 90.0, 11040.0) is None
    assert required_charge_seconds(40.0, 80.0, 60.0, 90.0, 0.0) is None


def test_next_departure() -> None:
    # 18:00 local is still ahead of 14:00 local: today.
    departure = next_departure("18:00:00", NOW, CPH)
    assert departure == datetime(2026, 7, 10, 18, 0, tzinfo=CPH)
    # 07:00 local already passed: tomorrow.
    departure = next_departure("07:00:00", NOW, CPH)
    assert departure == datetime(2026, 7, 11, 7, 0, tzinfo=CPH)
    assert next_departure(None, NOW, CPH) is None
    assert next_departure("not a time", NOW, CPH) is None


def test_plan_grid_windows_picks_cheapest() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    deadline = datetime(2026, 7, 10, 18, 0, tzinfo=CPH)  # hours 14-17 available
    plan = plan_grid_windows(intervals, NOW, deadline, needed_seconds=3600.0)
    assert plan is not None
    # Hour 14 (price 14) is the cheapest in the window and contains "now".
    assert plan.charge_now
    assert not plan.deadline_pressure
    assert plan.next_window_start is None


def test_plan_grid_windows_waits_for_cheaper_window() -> None:
    prices = [float(i) for i in range(24)]
    prices[15] = 1.0  # hour 15 is by far the cheapest in the window
    intervals = price_intervals(prices)
    deadline = datetime(2026, 7, 10, 18, 0, tzinfo=CPH)
    plan = plan_grid_windows(intervals, NOW, deadline, needed_seconds=3600.0)
    assert plan is not None
    assert not plan.charge_now
    assert plan.next_window_start == datetime(2026, 7, 10, 15, 0, tzinfo=CPH)


def test_plan_grid_windows_deadline_pressure() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    deadline = datetime(2026, 7, 10, 16, 0, tzinfo=CPH)  # only 2 hours left
    plan = plan_grid_windows(intervals, NOW, deadline, needed_seconds=3.0 * 3600.0)
    assert plan is not None
    assert plan.deadline_pressure


def test_plan_grid_windows_without_data() -> None:
    assert plan_grid_windows([], NOW, None, 3600.0) is None
    assert plan_grid_windows([], NOW, NOW, 3600.0) is None
    intervals = price_intervals([1.0] * 24)
    assert plan_grid_windows(intervals, NOW, None, 3600.0) is None
    assert plan_grid_windows(intervals, NOW, NOW + timedelta(hours=2), 0.0) is None


def test_deadline_pressure_charges_despite_expensive_price() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(
            solar_power_w=0.0,
            ev_soc=10.0,
            ev_min_soc=50.0,
            current_price=14.0,
        ),
        intervals,
        settings(
            strategy=Strategy.PRICE_AWARE,
            ev_battery_capacity_kwh=60.0,
            ev_departure_time="16:00:00",  # ~7 h needed, 2 h left
        ),
        data_ready=True,
        local_tz=CPH,
    )
    assert decision.recommendation is Recommendation.GRID_CHARGE_DEADLINE
    assert decision.recommended_ev_current_a == 16.0
    assert decision.should_start_ev


def test_planned_window_charges_at_normal_price() -> None:
    """Below min: the cheapest hour before departure is used even if the
    price is only 'normal' relative to the whole day."""
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(
            solar_power_w=0.0,
            ev_soc=40.0,
            ev_min_soc=50.0,
            current_price=14.0,  # normal classification
        ),
        intervals,
        settings(
            strategy=Strategy.PRICE_AWARE,
            ev_battery_capacity_kwh=60.0,
            ev_departure_time="18:00:00",
        ),
        data_ready=True,
        local_tz=CPH,
    )
    assert decision.recommendation is Recommendation.GRID_CHARGE_PLANNED


def test_waiting_for_planned_window_sets_next_action() -> None:
    prices = [float(i) for i in range(24)]
    prices[15] = 1.0  # the cheap window is at 15:00
    intervals = price_intervals(prices)
    decision = evaluate(
        snapshot(
            solar_power_w=0.0,
            ev_soc=40.0,
            ev_min_soc=50.0,
            current_price=14.0,
        ),
        intervals,
        settings(
            strategy=Strategy.PRICE_AWARE,
            ev_battery_capacity_kwh=6.0,  # small need: fits in one hour
            ev_departure_time="18:00:00",
        ),
        data_ready=True,
        local_tz=CPH,
    )
    assert decision.recommendation is Recommendation.NO_SURPLUS
    assert decision.next_action_at == datetime(2026, 7, 10, 15, 0, tzinfo=CPH)


def test_balanced_tops_up_on_very_cheap_without_plan_data() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(solar_power_w=0.0, ev_soc=60.0, ev_min_soc=40.0, current_price=0.5),
        intervals,
        settings(strategy=Strategy.BALANCED),
        data_ready=True,
        local_tz=CPH,
    )
    assert decision.recommendation is Recommendation.GRID_CHARGE_CHEAP


def test_price_aware_does_not_top_up_above_min() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(solar_power_w=0.0, ev_soc=60.0, ev_min_soc=40.0, current_price=0.5),
        intervals,
        settings(strategy=Strategy.PRICE_AWARE),
        data_ready=True,
        local_tz=CPH,
    )
    assert decision.recommendation is Recommendation.NO_SURPLUS


def test_balanced_planned_top_up_toward_target() -> None:
    intervals = price_intervals([float(i) for i in range(24)])
    decision = evaluate(
        snapshot(
            solar_power_w=0.0,
            ev_soc=60.0,
            ev_min_soc=40.0,
            current_price=14.0,
        ),
        intervals,
        settings(
            strategy=Strategy.BALANCED,
            ev_battery_capacity_kwh=60.0,
            ev_departure_time="18:00:00",
        ),
        data_ready=True,
        local_tz=CPH,
    )
    assert decision.recommendation is Recommendation.GRID_CHARGE_PLANNED
