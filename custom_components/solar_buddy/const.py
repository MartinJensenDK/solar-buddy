"""Constants for the Solar Buddy integration.

This module is intentionally free of Home Assistant imports so the pure
calculation modules (models, normalization, price_parser, optimizer) can be
unit-tested without a Home Assistant runtime.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

DOMAIN: Final = "solar_buddy"

# ---------------------------------------------------------------------------
# Config entry data keys (entity mapping chosen in the config flow)
# ---------------------------------------------------------------------------
CONF_SOLAR_PRODUCTION_ENTITY: Final = "solar_production_entity"
CONF_HOUSE_CONSUMPTION_ENTITY: Final = "house_consumption_entity"

CONF_BATTERY_ENABLED: Final = "battery_enabled"
CONF_BATTERY_SOC_ENTITY: Final = "battery_soc_entity"
CONF_BATTERY_POWER_MODE: Final = "battery_power_mode"
CONF_BATTERY_POWER_ENTITY: Final = "battery_power_entity"
CONF_BATTERY_POWER_SIGN: Final = "battery_power_sign"
CONF_BATTERY_CHARGE_POWER_ENTITY: Final = "battery_charge_power_entity"
CONF_BATTERY_DISCHARGE_POWER_ENTITY: Final = "battery_discharge_power_entity"
CONF_BATTERY_CHARGING_ENABLED_ENTITY: Final = "battery_charging_enabled_entity"
CONF_BATTERY_CHARGE_LIMIT_ENTITY: Final = "battery_charge_limit_entity"

CONF_EV_CHARGER_ENABLED: Final = "ev_charger_enabled"
CONF_EV_CONTROL_TYPE: Final = "ev_control_type"
CONF_EV_CHARGER_SWITCH_ENTITY: Final = "ev_charger_switch_entity"
CONF_EV_CHARGER_START_ENTITY: Final = "ev_charger_start_entity"
CONF_EV_CHARGER_STOP_ENTITY: Final = "ev_charger_stop_entity"
CONF_EV_CHARGER_CURRENT_ENTITY: Final = "ev_charger_current_entity"
CONF_EV_CABLE_CONNECTION_ENTITY: Final = "ev_cable_connection_entity"
CONF_EV_CONNECTED_STATES: Final = "ev_connected_states"
CONF_EV_SOC_ENTITY: Final = "ev_soc_entity"
CONF_EV_MIN_SOC_ENTITY: Final = "ev_min_soc_entity"

CONF_ELECTRICITY_PRICE_ENTITY: Final = "electricity_price_entity"
# Optional switch that allows/blocks exporting surplus to the grid.
CONF_GRID_EXPORT_SWITCH_ENTITY: Final = "grid_export_switch_entity"

# ---------------------------------------------------------------------------
# Options keys (behavior settings in the options flow)
# ---------------------------------------------------------------------------
CONF_EV_MIN_CURRENT: Final = "ev_min_current"
CONF_EV_MAX_CURRENT: Final = "ev_max_current"
CONF_EV_CURRENT_STEP: Final = "ev_current_step"
CONF_EV_PHASES: Final = "ev_phases"
CONF_EV_VOLTAGE: Final = "ev_voltage"
CONF_EV_START_DELAY: Final = "ev_start_delay"
CONF_EV_STOP_DELAY: Final = "ev_stop_delay"
CONF_EV_ADJUSTMENT_INTERVAL: Final = "ev_adjustment_interval"
CONF_EV_POWER_RESERVE: Final = "ev_power_reserve"

CONF_BATTERY_RESERVE_SOC: Final = "battery_reserve_soc"
CONF_BATTERY_TARGET_SOC: Final = "battery_target_soc"
CONF_EV_TARGET_SOC: Final = "ev_target_soc"
CONF_EV_BATTERY_CAPACITY_KWH: Final = "ev_battery_capacity_kwh"
CONF_EV_CHARGING_EFFICIENCY: Final = "ev_charging_efficiency"
CONF_EV_DEPARTURE_TIME: Final = "ev_departure_time"
CONF_CHEAP_PRICE_PERCENTILE: Final = "cheap_price_percentile"
CONF_EXPENSIVE_PRICE_PERCENTILE: Final = "expensive_price_percentile"
CONF_MANUAL_OVERRIDE_PAUSE: Final = "manual_override_pause"
CONF_MINIMUM_COMMAND_INTERVAL: Final = "minimum_command_interval"
CONF_DATA_STALE_TIMEOUT: Final = "data_stale_timeout"
CONF_EVALUATION_INTERVAL: Final = "evaluation_interval"

# EV charging schedule: days and a daily time window where charging may run.
CONF_EV_ALLOWED_DAYS: Final = "ev_allowed_days"
CONF_EV_SCHEDULE_START: Final = "ev_schedule_start"
CONF_EV_SCHEDULE_END: Final = "ev_schedule_end"

# Export is blocked while the current price is at or below this threshold
# (in the price sensor's own unit; no hardcoded currency).
CONF_EXPORT_PRICE_THRESHOLD: Final = "export_price_threshold"

WEEKDAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Start == end means the whole day is allowed.
DEFAULT_EV_SCHEDULE_START: Final = "00:00:00"
DEFAULT_EV_SCHEDULE_END: Final = "00:00:00"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_EV_MIN_CURRENT: Final = 6.0  # A
DEFAULT_EV_MAX_CURRENT: Final = 16.0  # A
DEFAULT_EV_CURRENT_STEP: Final = 1.0  # A
DEFAULT_EV_PHASES: Final = 1
DEFAULT_EV_VOLTAGE: Final = 230.0  # V
DEFAULT_EV_START_DELAY: Final = 60  # s
DEFAULT_EV_STOP_DELAY: Final = 120  # s
DEFAULT_EV_ADJUSTMENT_INTERVAL: Final = 60  # s
DEFAULT_EV_POWER_RESERVE: Final = 200.0  # W

DEFAULT_BATTERY_RESERVE_SOC: Final = 20.0  # %
DEFAULT_BATTERY_TARGET_SOC: Final = 100.0  # %
DEFAULT_EV_TARGET_SOC: Final = 80.0  # %
DEFAULT_EV_CHARGING_EFFICIENCY: Final = 90.0  # %
DEFAULT_CHEAP_PRICE_PERCENTILE: Final = 25.0
DEFAULT_EXPENSIVE_PRICE_PERCENTILE: Final = 75.0
DEFAULT_MANUAL_OVERRIDE_PAUSE: Final = 15  # minutes
DEFAULT_MINIMUM_COMMAND_INTERVAL: Final = 60  # s
DEFAULT_DATA_STALE_TIMEOUT: Final = 300  # s
DEFAULT_EVALUATION_INTERVAL: Final = 30  # s

# Absolute limits used by options-flow validation.
MIN_ALLOWED_EV_CURRENT: Final = 5.0  # A; some chargers support down to 5 A
MAX_ALLOWED_EV_CURRENT: Final = 64.0  # A
VALID_EV_PHASES: Final = (1, 3)
# Covers common single-phase grids (100-127 V) and European 230/240 V.
MIN_ALLOWED_EV_VOLTAGE: Final = 100.0
MAX_ALLOWED_EV_VOLTAGE: Final = 400.0

DEFAULT_EV_CONNECTED_STATES: Final = (
    "connected",
    "charging",
    "connected_charging",
    "connected_finished",
    "ready",
    "on",
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class Strategy(StrEnum):
    """Operating strategy for Solar Buddy."""

    SOLAR_ONLY = "solar_only"
    PRICE_AWARE = "price_aware"
    BALANCED = "balanced"
    MONITOR_ONLY = "monitor_only"


class Priority(StrEnum):
    """How solar surplus is shared between the house battery and the EV."""

    BATTERY_FIRST = "battery_first"
    EV_FIRST = "ev_first"
    BALANCED = "balanced"


class BatteryPowerMode(StrEnum):
    """How the house battery power is measured."""

    SIGNED = "signed"
    SEPARATE = "separate"
    NONE = "none"


class BatteryPowerSign(StrEnum):
    """Meaning of the sign on a signed battery power sensor."""

    POSITIVE_IS_CHARGING = "positive_is_charging"
    POSITIVE_IS_DISCHARGING = "positive_is_discharging"


class EvControlType(StrEnum):
    """How EV charging is started and stopped."""

    SWITCH = "switch"
    START_STOP = "start_stop"


class PriceLevel(StrEnum):
    """Relative classification of the current electricity price."""

    VERY_CHEAP = "very_cheap"
    CHEAP = "cheap"
    NORMAL = "normal"
    EXPENSIVE = "expensive"
    VERY_EXPENSIVE = "very_expensive"
    UNKNOWN = "unknown"


class SolarBuddyStatus(StrEnum):
    """Overall status shown on the status sensor."""

    MONITORING = "monitoring"
    ACTIVE = "active"
    PAUSED_MANUAL_OVERRIDE = "paused_manual_override"
    WAITING_FOR_DATA = "waiting_for_data"
    STALE_DATA = "stale_data"


class Recommendation(StrEnum):
    """Translation keys for the current recommendation/explanation."""

    DATA_NOT_READY = "data_not_ready"
    NO_EV_CONFIGURED = "no_ev_configured"
    EV_NOT_CONNECTED = "ev_not_connected"
    EV_TARGET_REACHED = "ev_target_reached"
    EV_CHARGE_RECOMMENDED = "ev_charge_recommended"
    GRID_CHARGE_CHEAP = "grid_charge_cheap"
    GRID_CHARGE_PLANNED = "grid_charge_planned"
    GRID_CHARGE_DEADLINE = "grid_charge_deadline"
    EV_BLOCKED_EXPENSIVE = "ev_blocked_expensive"
    EV_BLOCKED_SCHEDULE = "ev_blocked_schedule"
    NO_SURPLUS = "no_surplus"
    SURPLUS_BELOW_MINIMUM = "surplus_below_minimum"


class CommandAction(StrEnum):
    """Actions the actuator layer can perform."""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    PRESS = "press"
    SET_VALUE = "set_value"
    SELECT_OPTION = "select_option"


class ActuatorCapability(StrEnum):
    """What Solar Buddy is allowed to do with a configured entity."""

    TOGGLE = "toggle"
    PRESS = "press"
    SET_VALUE = "set_value"
    SELECT = "select"
    READ_ONLY = "read_only"
    MISSING = "missing"
