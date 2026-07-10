"""Typed models for normalized data flowing through Solar Buddy.

All models are plain dataclasses without Home Assistant dependencies so the
optimizer and price parser can be tested as pure Python.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .const import (
    CONF_BATTERY_RESERVE_SOC,
    CONF_BATTERY_TARGET_SOC,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DATA_STALE_TIMEOUT,
    CONF_EV_ADJUSTMENT_INTERVAL,
    CONF_EV_ALLOWED_DAYS,
    CONF_EV_BATTERY_CAPACITY_KWH,
    CONF_EV_CHARGING_EFFICIENCY,
    CONF_EV_CURRENT_STEP,
    CONF_EV_DEPARTURE_TIME,
    CONF_EV_MAX_CURRENT,
    CONF_EV_MIN_CURRENT,
    CONF_EV_PHASES,
    CONF_EV_POWER_RESERVE,
    CONF_EV_SCHEDULE_END,
    CONF_EV_SCHEDULE_START,
    CONF_EV_START_DELAY,
    CONF_EV_STOP_DELAY,
    CONF_EV_TARGET_SOC,
    CONF_EV_VOLTAGE,
    CONF_EVALUATION_INTERVAL,
    CONF_EXPENSIVE_PRICE_PERCENTILE,
    CONF_EXPORT_PRICE_THRESHOLD,
    CONF_MANUAL_OVERRIDE_PAUSE,
    CONF_MINIMUM_COMMAND_INTERVAL,
    DEFAULT_BATTERY_RESERVE_SOC,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DATA_STALE_TIMEOUT,
    DEFAULT_EV_ADJUSTMENT_INTERVAL,
    DEFAULT_EV_CHARGING_EFFICIENCY,
    DEFAULT_EV_CURRENT_STEP,
    DEFAULT_EV_MAX_CURRENT,
    DEFAULT_EV_MIN_CURRENT,
    DEFAULT_EV_PHASES,
    DEFAULT_EV_POWER_RESERVE,
    DEFAULT_EV_SCHEDULE_END,
    DEFAULT_EV_SCHEDULE_START,
    DEFAULT_EV_START_DELAY,
    DEFAULT_EV_STOP_DELAY,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_EV_VOLTAGE,
    DEFAULT_EVALUATION_INTERVAL,
    DEFAULT_EXPENSIVE_PRICE_PERCENTILE,
    DEFAULT_MANUAL_OVERRIDE_PAUSE,
    DEFAULT_MINIMUM_COMMAND_INTERVAL,
    WEEKDAYS,
    CommandAction,
    Priority,
    Recommendation,
    SolarBuddyStatus,
    Strategy,
)


@dataclass(slots=True)
class EnergySnapshot:
    """Normalized view of all input entities at one point in time.

    All power values are in watts and never negative. SoC values are percent
    in the 0-100 range or ``None`` when unknown.
    """

    timestamp: datetime
    solar_power_w: float
    house_consumption_w: float
    battery_charge_power_w: float
    battery_discharge_power_w: float
    battery_soc: float | None
    ev_soc: float | None
    ev_min_soc: float | None
    ev_connected: bool
    ev_charging: bool
    ev_current_a: float | None
    current_price: float | None


@dataclass(slots=True)
class PriceInterval:
    """One price interval (hourly or 15 minutes) from the price sensor."""

    start: datetime
    end: datetime
    price: float


@dataclass(slots=True)
class PriceData:
    """Parsed electricity price information."""

    currency: str | None = None
    unit: str | None = None
    current_price: float | None = None
    intervals: list[PriceInterval] = field(default_factory=list)

    @property
    def has_intervals(self) -> bool:
        """Return True when at least one price interval is known."""
        return bool(self.intervals)


@dataclass(slots=True)
class OptimizationSettings:
    """All settings that influence an optimization decision."""

    strategy: Strategy = Strategy.MONITOR_ONLY
    priority: Priority = Priority.BATTERY_FIRST
    automatic_control: bool = False
    manual_override: bool = False

    ev_min_current: float = DEFAULT_EV_MIN_CURRENT
    ev_max_current: float = DEFAULT_EV_MAX_CURRENT
    ev_current_step: float = DEFAULT_EV_CURRENT_STEP
    ev_phases: int = DEFAULT_EV_PHASES
    ev_voltage: float = DEFAULT_EV_VOLTAGE
    ev_start_delay_s: int = DEFAULT_EV_START_DELAY
    ev_stop_delay_s: int = DEFAULT_EV_STOP_DELAY
    ev_adjustment_interval_s: int = DEFAULT_EV_ADJUSTMENT_INTERVAL
    ev_power_reserve_w: float = DEFAULT_EV_POWER_RESERVE

    battery_reserve_soc: float = DEFAULT_BATTERY_RESERVE_SOC
    battery_target_soc: float = DEFAULT_BATTERY_TARGET_SOC
    ev_target_soc: float = DEFAULT_EV_TARGET_SOC
    ev_battery_capacity_kwh: float | None = None
    ev_charging_efficiency: float = DEFAULT_EV_CHARGING_EFFICIENCY
    ev_departure_time: str | None = None

    cheap_price_percentile: float = DEFAULT_CHEAP_PRICE_PERCENTILE
    expensive_price_percentile: float = DEFAULT_EXPENSIVE_PRICE_PERCENTILE

    ev_allowed_days: tuple[str, ...] = WEEKDAYS
    ev_schedule_start: str = DEFAULT_EV_SCHEDULE_START
    ev_schedule_end: str = DEFAULT_EV_SCHEDULE_END
    export_price_threshold: float | None = None

    manual_override_pause_min: int = DEFAULT_MANUAL_OVERRIDE_PAUSE
    minimum_command_interval_s: int = DEFAULT_MINIMUM_COMMAND_INTERVAL
    data_stale_timeout_s: int = DEFAULT_DATA_STALE_TIMEOUT
    evaluation_interval_s: int = DEFAULT_EVALUATION_INTERVAL

    ev_configured: bool = False
    battery_configured: bool = False

    @classmethod
    def from_options(  # noqa: PLR0913 - one keyword per runtime toggle is clearer
        cls,
        options: Mapping[str, Any],
        *,
        strategy: Strategy,
        priority: Priority,
        automatic_control: bool,
        ev_configured: bool,
        battery_configured: bool,
        manual_override: bool = False,
    ) -> OptimizationSettings:
        """Build settings from a config entry's options mapping."""
        return cls(
            strategy=strategy,
            priority=priority,
            automatic_control=automatic_control,
            manual_override=manual_override,
            ev_min_current=float(
                options.get(CONF_EV_MIN_CURRENT, DEFAULT_EV_MIN_CURRENT)
            ),
            ev_max_current=float(
                options.get(CONF_EV_MAX_CURRENT, DEFAULT_EV_MAX_CURRENT)
            ),
            ev_current_step=float(
                options.get(CONF_EV_CURRENT_STEP, DEFAULT_EV_CURRENT_STEP)
            ),
            ev_phases=int(options.get(CONF_EV_PHASES, DEFAULT_EV_PHASES)),
            ev_voltage=float(options.get(CONF_EV_VOLTAGE, DEFAULT_EV_VOLTAGE)),
            ev_start_delay_s=int(
                options.get(CONF_EV_START_DELAY, DEFAULT_EV_START_DELAY)
            ),
            ev_stop_delay_s=int(
                options.get(CONF_EV_STOP_DELAY, DEFAULT_EV_STOP_DELAY)
            ),
            ev_adjustment_interval_s=int(
                options.get(CONF_EV_ADJUSTMENT_INTERVAL, DEFAULT_EV_ADJUSTMENT_INTERVAL)
            ),
            ev_power_reserve_w=float(
                options.get(CONF_EV_POWER_RESERVE, DEFAULT_EV_POWER_RESERVE)
            ),
            battery_reserve_soc=float(
                options.get(CONF_BATTERY_RESERVE_SOC, DEFAULT_BATTERY_RESERVE_SOC)
            ),
            battery_target_soc=float(
                options.get(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC)
            ),
            ev_target_soc=float(options.get(CONF_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC)),
            ev_battery_capacity_kwh=(
                float(options[CONF_EV_BATTERY_CAPACITY_KWH])
                if options.get(CONF_EV_BATTERY_CAPACITY_KWH) is not None
                else None
            ),
            ev_charging_efficiency=float(
                options.get(CONF_EV_CHARGING_EFFICIENCY, DEFAULT_EV_CHARGING_EFFICIENCY)
            ),
            ev_departure_time=options.get(CONF_EV_DEPARTURE_TIME),
            cheap_price_percentile=float(
                options.get(CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE)
            ),
            expensive_price_percentile=float(
                options.get(
                    CONF_EXPENSIVE_PRICE_PERCENTILE,
                    DEFAULT_EXPENSIVE_PRICE_PERCENTILE,
                )
            ),
            ev_allowed_days=tuple(options.get(CONF_EV_ALLOWED_DAYS, WEEKDAYS)),
            ev_schedule_start=str(
                options.get(CONF_EV_SCHEDULE_START, DEFAULT_EV_SCHEDULE_START)
            ),
            ev_schedule_end=str(
                options.get(CONF_EV_SCHEDULE_END, DEFAULT_EV_SCHEDULE_END)
            ),
            export_price_threshold=(
                float(options[CONF_EXPORT_PRICE_THRESHOLD])
                if options.get(CONF_EXPORT_PRICE_THRESHOLD) is not None
                else None
            ),
            manual_override_pause_min=int(
                options.get(CONF_MANUAL_OVERRIDE_PAUSE, DEFAULT_MANUAL_OVERRIDE_PAUSE)
            ),
            minimum_command_interval_s=int(
                options.get(
                    CONF_MINIMUM_COMMAND_INTERVAL, DEFAULT_MINIMUM_COMMAND_INTERVAL
                )
            ),
            data_stale_timeout_s=int(
                options.get(CONF_DATA_STALE_TIMEOUT, DEFAULT_DATA_STALE_TIMEOUT)
            ),
            evaluation_interval_s=int(
                options.get(CONF_EVALUATION_INTERVAL, DEFAULT_EVALUATION_INTERVAL)
            ),
            ev_configured=ev_configured,
            battery_configured=battery_configured,
        )

    def charger_power_w(self, current_a: float) -> float:
        """Approximate charger power draw at a given current."""
        return self.ev_phases * self.ev_voltage * current_a


@dataclass(slots=True)
class OptimizationDecision:
    """The outcome of one optimizer evaluation.

    ``recommendation`` is a translation key; ``reason_placeholders`` carries
    the dynamic values belonging to that key so the frontend text can be
    localized without hardcoded strings in Python.
    """

    strategy: Strategy
    status: SolarBuddyStatus
    recommendation: Recommendation
    reason_placeholders: dict[str, str] = field(default_factory=dict)
    solar_surplus_w: float = 0.0
    available_ev_power_w: float = 0.0
    recommended_ev_current_a: float = 0.0
    should_start_ev: bool = False
    should_stop_ev: bool = False
    should_change_ev_current: bool = False
    # Battery/export: None means "leave the entity untouched".
    should_enable_battery_charging: bool | None = None
    battery_charge_limit_pct: float | None = None
    should_allow_export: bool | None = None
    price_level: str | None = None
    next_action_at: datetime | None = None
    data_ready: bool = False


@dataclass(slots=True)
class ActuatorCommand:
    """One command for the actuator layer to execute."""

    entity_id: str
    action: CommandAction
    value: float | str | None = None
