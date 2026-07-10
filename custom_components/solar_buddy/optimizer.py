"""The deterministic optimization engine.

Pure module: receives normalized data (EnergySnapshot, price intervals,
OptimizationSettings) and returns an OptimizationDecision. It never calls
Home Assistant services; command execution belongs to the actuator layer.

Power balance
-------------
The configured house consumption sensor must measure the house's base load
WITHOUT the EV charger and without battery charge/discharge flows. From that:

    solar_surplus_w = solar_power_w - house_consumption_w

Because the EV charger's own draw is not part of the house consumption, a
charging EV does not eat its own surplus: the surplus is what the EV and the
battery can share. Battery flows are accounted for explicitly per priority:

* ``battery_first``: the battery's current charge draw is reserved before
  the EV gets the remainder.
* ``ev_first``: the EV may use the full surplus; the battery gets whatever
  the EV leaves (its charging is expected to yield when surplus drops).
* ``balanced``: EV is boosted above minimum SoC deficits, battery is boosted
  below its reserve SoC; otherwise behaves like ``battery_first``.

Battery discharge is deliberately NOT added to the surplus available for the
EV: discharging the house battery into the car is never treated as free solar
power. Grid charging is only ever recommended by the price rules (cheap
intervals + EV below its minimum SoC).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo

from .const import (
    PriceLevel,
    Priority,
    Recommendation,
    SolarBuddyStatus,
    Strategy,
)
from .models import (
    EnergySnapshot,
    OptimizationDecision,
    OptimizationSettings,
    PriceInterval,
)
from .price_parser import classify_price

_GRID_CHARGE_LEVELS = (PriceLevel.VERY_CHEAP, PriceLevel.CHEAP)
_EXPENSIVE_LEVELS = (PriceLevel.EXPENSIVE, PriceLevel.VERY_EXPENSIVE)
_PRICE_STRATEGIES = (Strategy.PRICE_AWARE, Strategy.BALANCED)


# ---------------------------------------------------------------------------
# Grid charging plan (pure helpers)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class GridChargePlan:
    """Result of planning grid charging into the cheapest intervals."""

    charge_now: bool
    deadline_pressure: bool
    next_window_start: datetime | None


def required_charge_seconds(
    current_soc: float | None,
    goal_soc: float | None,
    capacity_kwh: float | None,
    efficiency_pct: float,
    charge_power_w: float,
) -> float | None:
    """Seconds of full-power charging needed to reach the goal SoC.

    Returns ``None`` when the necessary data is missing, 0.0 when the goal
    is already reached.
    """
    if (
        current_soc is None
        or goal_soc is None
        or capacity_kwh is None
        or capacity_kwh <= 0
        or efficiency_pct <= 0
        or charge_power_w <= 0
    ):
        return None
    missing_pct = goal_soc - current_soc
    if missing_pct <= 0:
        return 0.0
    energy_wh = missing_pct / 100.0 * capacity_kwh * 1000.0 / (efficiency_pct / 100.0)
    return energy_wh / charge_power_w * 3600.0


def next_departure(
    departure: str | None, now: datetime, local_tz: tzinfo | None
) -> datetime | None:
    """Next occurrence of a local HH:MM[:SS] departure time, or None."""
    if not departure:
        return None
    try:
        departure_time = time.fromisoformat(departure)
    except ValueError:
        return None
    zone = local_tz or now.tzinfo
    local_now = now.astimezone(zone)
    candidate = datetime.combine(local_now.date(), departure_time, tzinfo=zone)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def plan_grid_windows(
    intervals: list[PriceInterval],
    now: datetime,
    deadline: datetime | None,
    needed_seconds: float | None,
) -> GridChargePlan | None:
    """Pick the cheapest intervals before the deadline to cover the need.

    Deterministic: candidates are the known price intervals clipped to
    [now, deadline), sorted by (price, start), and chosen greedily until the
    needed charging time is covered. ``deadline_pressure`` means the
    remaining time barely (or no longer) covers the need, so charging must
    run continuously regardless of price.
    """
    if needed_seconds is None or deadline is None or needed_seconds <= 0.0:
        return None
    windows: list[tuple[float, datetime, datetime]] = []
    for interval in intervals:
        start = max(interval.start, now)
        end = min(interval.end, deadline)
        if end > start:
            windows.append((interval.price, start, end))
    if not windows:
        return None

    available_seconds = sum(
        (end - start).total_seconds() for _, start, end in windows
    )
    deadline_pressure = needed_seconds >= available_seconds

    chosen: list[tuple[datetime, datetime]] = []
    accumulated = 0.0
    for _, start, end in sorted(windows, key=lambda window: (window[0], window[1])):
        if accumulated >= needed_seconds:
            break
        chosen.append((start, end))
        accumulated += (end - start).total_seconds()

    charge_now = any(start <= now < end for start, end in chosen)
    future_starts = [start for start, _ in chosen if start > now]
    return GridChargePlan(
        charge_now=charge_now,
        deadline_pressure=deadline_pressure,
        next_window_start=min(future_starts) if future_starts else None,
    )


def recommended_current(available_power_w: float, settings: OptimizationSettings) -> float:
    """Compute the recommended EV current for the available power.

    Floors to the configured current step and clamps to [min, max]. Returns
    0.0 when the available power cannot sustain the minimum current.
    """
    step = settings.ev_current_step
    per_ampere = settings.ev_phases * settings.ev_voltage
    if step <= 0.0 or per_ampere <= 0.0:
        return 0.0
    current = math.floor(available_power_w / (per_ampere * step)) * step
    if current < settings.ev_min_current:
        return 0.0
    return min(current, settings.ev_max_current)


def _status_for(settings: OptimizationSettings, data_ready: bool, stale: bool) -> SolarBuddyStatus:
    if stale:
        return SolarBuddyStatus.STALE_DATA
    if not data_ready:
        return SolarBuddyStatus.WAITING_FOR_DATA
    if settings.automatic_control and settings.strategy is not Strategy.MONITOR_ONLY:
        if settings.manual_override:
            return SolarBuddyStatus.PAUSED_MANUAL_OVERRIDE
        return SolarBuddyStatus.ACTIVE
    return SolarBuddyStatus.MONITORING


def _available_ev_power(
    snapshot: EnergySnapshot, settings: OptimizationSettings, surplus_w: float
) -> float:
    """Distribute the surplus between battery and EV according to priority."""
    surplus_w = max(surplus_w, 0.0)
    battery_draw = snapshot.battery_charge_power_w

    if not settings.battery_configured or settings.priority is Priority.EV_FIRST:
        share = surplus_w
    elif settings.priority is Priority.BALANCED:
        ev_needs_boost = (
            snapshot.ev_soc is not None
            and snapshot.ev_min_soc is not None
            and snapshot.ev_soc < snapshot.ev_min_soc
        )
        battery_needs_boost = (
            snapshot.battery_soc is not None
            and snapshot.battery_soc < settings.battery_reserve_soc
        )
        if battery_needs_boost and not ev_needs_boost:
            share = max(surplus_w - battery_draw, 0.0)
        elif ev_needs_boost:
            share = surplus_w
        else:
            share = max(surplus_w - battery_draw, 0.0)
    else:  # battery_first
        share = max(surplus_w - battery_draw, 0.0)

    return max(share - settings.ev_power_reserve_w, 0.0)


def evaluate(
    snapshot: EnergySnapshot,
    price_intervals: list[PriceInterval],
    settings: OptimizationSettings,
    *,
    data_ready: bool,
    stale: bool = False,
    local_tz: tzinfo | None = None,
) -> OptimizationDecision:
    """Evaluate one snapshot and produce a decision.

    ``data_ready`` reflects whether the mandatory power sensors delivered
    valid, fresh values; when False no control action is ever proposed.
    """
    decision = _evaluate_ev(
        snapshot,
        price_intervals,
        settings,
        data_ready=data_ready,
        stale=stale,
        local_tz=local_tz,
    )
    _apply_battery_decision(decision, snapshot, settings)
    _apply_export_decision(decision, snapshot, settings)
    return decision


def charging_allowed_now(
    now: datetime,
    allowed_days: tuple[str, ...],
    window_start: str,
    window_end: str,
    local_tz: tzinfo | None,
) -> bool:
    """Is EV charging allowed by the user's schedule right now?

    ``now`` is converted to local time; the weekday must be among
    ``allowed_days`` and the local time inside [start, end). Start == end
    means the whole day; start > end means the window wraps past midnight
    (the weekday check applies to the current local day). Unparsable times
    fail open (allowed) so a broken option never blocks charging silently.
    """
    local_now = now.astimezone(local_tz) if local_tz else now
    weekday_keys = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    if weekday_keys[local_now.weekday()] not in allowed_days:
        return False
    try:
        start = time.fromisoformat(window_start)
        end = time.fromisoformat(window_end)
    except ValueError:
        return True
    if start == end:
        return True
    current = local_now.time()
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _apply_export_decision(
    decision: OptimizationDecision,
    snapshot: EnergySnapshot,
    settings: OptimizationSettings,
) -> None:
    """Block grid export while the price is at or below the threshold.

    The threshold is in the price sensor's own unit (currency-agnostic).
    With no threshold or no known price, the export entity is left alone.
    """
    if not decision.data_ready or settings.export_price_threshold is None:
        return
    if snapshot.current_price is None:
        return
    decision.should_allow_export = (
        snapshot.current_price > settings.export_price_threshold
    )


def _apply_battery_decision(
    decision: OptimizationDecision,
    snapshot: EnergySnapshot,
    settings: OptimizationSettings,
) -> None:
    """Fill the battery parts of a decision (charging on/off + limit).

    Deterministic rules, evaluated after the EV decision so the EV/battery
    priority can be honored:

    * at or above the target SoC: charging off,
    * below the reserve SoC: charging on (safety floor, beats priority),
    * ``ev_first`` while the EV is actively (about to be) charging: charging
      off, so the whole surplus goes to the car,
    * otherwise below target: charging on.

    The charge limit is kept at the configured target SoC so the inverter
    enforces the target itself. Unknown SoC leaves the toggle untouched.
    """
    if not settings.battery_configured or not decision.data_ready:
        return
    decision.battery_charge_limit_pct = settings.battery_target_soc

    soc = snapshot.battery_soc
    if soc is None:
        return
    if soc >= settings.battery_target_soc:
        decision.should_enable_battery_charging = False
        return
    if soc < settings.battery_reserve_soc:
        decision.should_enable_battery_charging = True
        return
    ev_active = decision.should_start_ev or (
        snapshot.ev_charging and not decision.should_stop_ev
    )
    if settings.priority is Priority.EV_FIRST and ev_active:
        decision.should_enable_battery_charging = False
        return
    decision.should_enable_battery_charging = True


def _evaluate_ev(
    snapshot: EnergySnapshot,
    price_intervals: list[PriceInterval],
    settings: OptimizationSettings,
    *,
    data_ready: bool,
    stale: bool = False,
    local_tz: tzinfo | None = None,
) -> OptimizationDecision:
    """The EV/surplus part of the evaluation."""
    surplus_w = snapshot.solar_power_w - snapshot.house_consumption_w
    status = _status_for(settings, data_ready, stale)

    price_level = classify_price(
        snapshot.current_price,
        price_intervals,
        settings.cheap_price_percentile,
        settings.expensive_price_percentile,
    )

    decision = OptimizationDecision(
        strategy=settings.strategy,
        status=status,
        recommendation=Recommendation.DATA_NOT_READY,
        solar_surplus_w=surplus_w,
        available_ev_power_w=0.0,
        price_level=price_level.value,
        data_ready=data_ready,
    )

    if not data_ready:
        return decision

    available_w = _available_ev_power(snapshot, settings, surplus_w)
    decision.available_ev_power_w = available_w
    current_a = recommended_current(available_w, settings)
    decision.recommended_ev_current_a = current_a

    if not settings.ev_configured:
        decision.recommendation = Recommendation.NO_EV_CONFIGURED
        return decision

    if not snapshot.ev_connected:
        decision.recommendation = Recommendation.EV_NOT_CONNECTED
        decision.should_stop_ev = snapshot.ev_charging
        decision.recommended_ev_current_a = 0.0
        return decision

    # The user's charging schedule beats everything, including grid-charge
    # deadlines: outside the allowed days/window the EV never charges.
    if not charging_allowed_now(
        snapshot.timestamp,
        settings.ev_allowed_days,
        settings.ev_schedule_start,
        settings.ev_schedule_end,
        local_tz,
    ):
        decision.recommendation = Recommendation.EV_BLOCKED_SCHEDULE
        decision.should_stop_ev = snapshot.ev_charging
        decision.recommended_ev_current_a = 0.0
        return decision

    if snapshot.ev_soc is not None and snapshot.ev_soc >= settings.ev_target_soc:
        decision.recommendation = Recommendation.EV_TARGET_REACHED
        decision.reason_placeholders = {
            "ev_soc": f"{snapshot.ev_soc:.0f}",
            "target_soc": f"{settings.ev_target_soc:.0f}",
        }
        decision.should_stop_ev = snapshot.ev_charging
        decision.recommended_ev_current_a = 0.0
        return decision

    # Price-driven grid charging (price_aware and balanced strategies).
    ev_below_min = (
        snapshot.ev_soc is not None
        and snapshot.ev_min_soc is not None
        and snapshot.ev_soc < snapshot.ev_min_soc
    )
    if settings.strategy in _PRICE_STRATEGIES and _apply_grid_charging(
        decision, snapshot, settings, price_intervals, price_level, ev_below_min, local_tz
    ):
        return decision

    # Price-aware strategies avoid discretionary charging in expensive hours.
    if (
        settings.strategy is Strategy.PRICE_AWARE
        and price_level in _EXPENSIVE_LEVELS
        and not ev_below_min
        and current_a <= 0.0
    ):
        decision.recommendation = Recommendation.EV_BLOCKED_EXPENSIVE
        decision.should_stop_ev = snapshot.ev_charging
        return decision

    if current_a > 0.0:
        decision.recommendation = Recommendation.EV_CHARGE_RECOMMENDED
        decision.reason_placeholders = {
            "current": f"{current_a:.0f}",
            "available": f"{available_w:.0f}",
        }
        decision.should_start_ev = not snapshot.ev_charging
        decision.should_change_ev_current = _needs_current_change(
            snapshot, settings, current_a
        )
        return decision

    if surplus_w <= 0.0:
        decision.recommendation = Recommendation.NO_SURPLUS
    else:
        decision.recommendation = Recommendation.SURPLUS_BELOW_MINIMUM
        decision.reason_placeholders = {
            "available": f"{available_w:.0f}",
            "minimum": f"{settings.charger_power_w(settings.ev_min_current):.0f}",
        }
    decision.should_stop_ev = snapshot.ev_charging
    return decision


def _apply_grid_charging(
    decision: OptimizationDecision,
    snapshot: EnergySnapshot,
    settings: OptimizationSettings,
    price_intervals: list[PriceInterval],
    price_level: PriceLevel,
    ev_below_min: bool,
    local_tz: tzinfo | None,
) -> bool:
    """Decide whether to grid-charge now; returns True when decided.

    Below the minimum SoC (both price strategies):

    1. deadline pressure — the time left before departure barely covers the
       need, so charge continuously regardless of price,
    2. the current price is classified (very) cheap,
    3. the current interval is one of the planned cheapest windows before
       departure (requires departure time + battery capacity).

    Between minimum and target SoC (balanced only): charge in the planned
    cheapest windows, or — without planning data — when the price is
    classified very cheap (which includes negative prices).

    When charging should wait for a later planned window, only
    ``next_action_at`` is set and the surplus logic still runs.
    """
    now = snapshot.timestamp
    departure = next_departure(settings.ev_departure_time, now, local_tz)
    max_power = settings.charger_power_w(settings.ev_max_current)

    if ev_below_min:
        needed = required_charge_seconds(
            snapshot.ev_soc,
            snapshot.ev_min_soc,
            settings.ev_battery_capacity_kwh,
            settings.ev_charging_efficiency,
            max_power,
        )
        plan = plan_grid_windows(price_intervals, now, departure, needed)
        if plan is not None and plan.deadline_pressure:
            _fill_grid_decision(
                decision, snapshot, settings, Recommendation.GRID_CHARGE_DEADLINE
            )
            return True
        if price_level in _GRID_CHARGE_LEVELS:
            _fill_grid_decision(
                decision, snapshot, settings, Recommendation.GRID_CHARGE_CHEAP
            )
            return True
        if plan is not None:
            if plan.charge_now:
                _fill_grid_decision(
                    decision, snapshot, settings, Recommendation.GRID_CHARGE_PLANNED
                )
                return True
            decision.next_action_at = plan.next_window_start
        return False

    # Above minimum: only the balanced strategy tops up toward the target.
    if settings.strategy is not Strategy.BALANCED or snapshot.ev_soc is None:
        return False
    needed = required_charge_seconds(
        snapshot.ev_soc,
        settings.ev_target_soc,
        settings.ev_battery_capacity_kwh,
        settings.ev_charging_efficiency,
        max_power,
    )
    plan = plan_grid_windows(price_intervals, now, departure, needed)
    if plan is not None:
        if plan.charge_now:
            _fill_grid_decision(
                decision, snapshot, settings, Recommendation.GRID_CHARGE_PLANNED
            )
            return True
        decision.next_action_at = plan.next_window_start
        return False
    if price_level is PriceLevel.VERY_CHEAP:
        _fill_grid_decision(
            decision, snapshot, settings, Recommendation.GRID_CHARGE_CHEAP
        )
        return True
    return False


def _fill_grid_decision(
    decision: OptimizationDecision,
    snapshot: EnergySnapshot,
    settings: OptimizationSettings,
    recommendation: Recommendation,
) -> None:
    """Fill a decision that grid-charges at maximum current."""
    decision.recommendation = recommendation
    decision.reason_placeholders = {
        "ev_soc": f"{snapshot.ev_soc:.0f}" if snapshot.ev_soc is not None else "?",
        "min_soc": (
            f"{snapshot.ev_min_soc:.0f}" if snapshot.ev_min_soc is not None else "?"
        ),
        "target_soc": f"{settings.ev_target_soc:.0f}",
    }
    decision.recommended_ev_current_a = settings.ev_max_current
    decision.should_start_ev = not snapshot.ev_charging
    decision.should_change_ev_current = _needs_current_change(
        snapshot, settings, settings.ev_max_current
    )


def _needs_current_change(
    snapshot: EnergySnapshot, settings: OptimizationSettings, target_a: float
) -> bool:
    """A new current is only worth sending if it differs by >= one step."""
    if not snapshot.ev_charging:
        return False
    if snapshot.ev_current_a is None:
        return True
    return abs(target_a - snapshot.ev_current_a) >= settings.ev_current_step
