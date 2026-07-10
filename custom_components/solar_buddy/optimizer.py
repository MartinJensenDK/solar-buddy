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
) -> OptimizationDecision:
    """Evaluate one snapshot and produce a decision.

    ``data_ready`` reflects whether the mandatory power sensors delivered
    valid, fresh values; when False no control action is ever proposed.
    """
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

    if snapshot.ev_soc is not None and snapshot.ev_soc >= settings.ev_target_soc:
        decision.recommendation = Recommendation.EV_TARGET_REACHED
        decision.reason_placeholders = {
            "ev_soc": f"{snapshot.ev_soc:.0f}",
            "target_soc": f"{settings.ev_target_soc:.0f}",
        }
        decision.should_stop_ev = snapshot.ev_charging
        decision.recommended_ev_current_a = 0.0
        return decision

    # Price-driven grid charging: allowed in price_aware and balanced when
    # the EV is below its minimum SoC and the price is (very) cheap.
    ev_below_min = (
        snapshot.ev_soc is not None
        and snapshot.ev_min_soc is not None
        and snapshot.ev_soc < snapshot.ev_min_soc
    )
    if (
        settings.strategy in (Strategy.PRICE_AWARE, Strategy.BALANCED)
        and ev_below_min
        and price_level in _GRID_CHARGE_LEVELS
    ):
        decision.recommendation = Recommendation.GRID_CHARGE_CHEAP
        decision.reason_placeholders = {
            "ev_soc": f"{snapshot.ev_soc:.0f}" if snapshot.ev_soc is not None else "?",
            "min_soc": (
                f"{snapshot.ev_min_soc:.0f}" if snapshot.ev_min_soc is not None else "?"
            ),
        }
        decision.recommended_ev_current_a = settings.ev_max_current
        decision.should_start_ev = not snapshot.ev_charging
        decision.should_change_ev_current = _needs_current_change(
            snapshot, settings, settings.ev_max_current
        )
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


def _needs_current_change(
    snapshot: EnergySnapshot, settings: OptimizationSettings, target_a: float
) -> bool:
    """A new current is only worth sending if it differs by >= one step."""
    if not snapshot.ev_charging:
        return False
    if snapshot.ev_current_a is None:
        return True
    return abs(target_a - snapshot.ev_current_a) >= settings.ev_current_step
