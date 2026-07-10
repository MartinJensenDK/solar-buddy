"""Config, reconfigure and options flows for Solar Buddy.

The setup is split into logical steps: energy sensors, battery, EV charger,
and electricity price. Only the two energy sensors are mandatory. All
entities are chosen with entity selectors; nothing assumes specific entity
ids. Validation is defensive: a temporarily unavailable entity does not block
setup (a warning is logged), but wrong units, energy sensors selected as
power sensors, and inconsistent combinations are rejected with field errors.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CHARGE_LIMIT_ENTITY,
    CONF_BATTERY_CHARGE_POWER_ENTITY,
    CONF_BATTERY_CHARGING_ENABLED_ENTITY,
    CONF_BATTERY_DISCHARGE_POWER_ENTITY,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_MODE,
    CONF_BATTERY_POWER_SIGN,
    CONF_BATTERY_RESERVE_SOC,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_TARGET_SOC,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DATA_STALE_TIMEOUT,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_EV_ADJUSTMENT_INTERVAL,
    CONF_EV_ALLOWED_DAYS,
    CONF_EV_BATTERY_CAPACITY_KWH,
    CONF_EV_CABLE_CONNECTION_ENTITY,
    CONF_EV_CHARGER_CURRENT_ENTITY,
    CONF_EV_CHARGER_ENABLED,
    CONF_EV_CHARGER_START_ENTITY,
    CONF_EV_CHARGER_STOP_ENTITY,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CHARGING_EFFICIENCY,
    CONF_EV_CONNECTED_STATES,
    CONF_EV_CONTROL_TYPE,
    CONF_EV_CURRENT_STEP,
    CONF_EV_DEPARTURE_TIME,
    CONF_EV_MAX_CURRENT,
    CONF_EV_MIN_CURRENT,
    CONF_EV_MIN_SOC_ENTITY,
    CONF_EV_PHASES,
    CONF_EV_POWER_RESERVE,
    CONF_EV_SCHEDULE_END,
    CONF_EV_SCHEDULE_START,
    CONF_EV_SOC_ENTITY,
    CONF_EV_START_DELAY,
    CONF_EV_STOP_DELAY,
    CONF_EV_TARGET_SOC,
    CONF_EV_VOLTAGE,
    CONF_EVALUATION_INTERVAL,
    CONF_EXPENSIVE_PRICE_PERCENTILE,
    CONF_EXPORT_PRICE_THRESHOLD,
    CONF_GRID_EXPORT_SWITCH_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_MANUAL_OVERRIDE_PAUSE,
    CONF_MINIMUM_COMMAND_INTERVAL,
    CONF_SOLAR_PRODUCTION_ENTITY,
    DEFAULT_BATTERY_RESERVE_SOC,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DATA_STALE_TIMEOUT,
    DEFAULT_EV_ADJUSTMENT_INTERVAL,
    DEFAULT_EV_CHARGING_EFFICIENCY,
    DEFAULT_EV_CONNECTED_STATES,
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
    DOMAIN,
    MAX_ALLOWED_EV_CURRENT,
    MAX_ALLOWED_EV_VOLTAGE,
    MIN_ALLOWED_EV_CURRENT,
    MIN_ALLOWED_EV_VOLTAGE,
    VALID_EV_PHASES,
    WEEKDAYS,
    BatteryPowerMode,
    BatteryPowerSign,
    EvControlType,
)
from .normalization import ENERGY_UNITS, UNKNOWN_STATES, parse_float

_LOGGER = logging.getLogger(__name__)

_POWER_UNITS = frozenset({"w", "watt", "kw", "kilowatt", "mw"})
_TOGGLE_DOMAINS = ["switch", "input_boolean"]
_START_STOP_DOMAINS = ["button", "input_button", "script", "switch"]
_NUMBER_DOMAINS = ["number", "input_number"]
_BATTERY_LIMIT_DOMAINS = ["number", "input_number", "sensor", "select"]
_BATTERY_TOGGLE_DOMAINS = ["switch", "input_boolean", "binary_sensor", "sensor"]


# ---------------------------------------------------------------------------
# Field validation helpers
# ---------------------------------------------------------------------------
def _validate_power_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Validate a power sensor selection; returns an error key or None.

    A missing or temporarily unavailable entity is accepted with a warning so
    users can complete setup while a device is briefly offline.
    """
    state = hass.states.get(entity_id)
    if state is None:
        _LOGGER.warning(
            "Entity %s does not currently exist; accepting it anyway", entity_id
        )
        return None
    if state.state.lower() in UNKNOWN_STATES:
        _LOGGER.warning(
            "Entity %s is currently %s; accepting it anyway", entity_id, state.state
        )
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit is not None:
        normalized = str(unit).strip().lower()
        if normalized in ENERGY_UNITS:
            return "energy_sensor_selected"
        if normalized not in _POWER_UNITS:
            return "not_a_power_sensor"
    elif parse_float(state.state) is None:
        return "not_a_power_sensor"
    return None


def _validate_percentage_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Validate that an entity looks like a percentage sensor."""
    state = hass.states.get(entity_id)
    if state is None or state.state.lower() in UNKNOWN_STATES:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit is not None and str(unit).strip() != "%":
        return "not_a_percentage"
    if unit is None and parse_float(state.state) is None:
        return "not_a_percentage"
    return None


def _validate_price_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """The price sensor needs at least a numeric state."""
    state = hass.states.get(entity_id)
    if state is None or state.state.lower() in UNKNOWN_STATES:
        _LOGGER.warning(
            "Price entity %s is currently unavailable; accepting it anyway", entity_id
        )
        return None
    if parse_float(state.state) is None and parse_float(
        state.attributes.get("current_price")
    ) is None:
        return "price_entity_invalid"
    return None


def validate_options(user_input: dict[str, Any]) -> dict[str, str]:
    """Cross-field validation shared by the options flow and tests."""
    errors: dict[str, str] = {}
    min_current = user_input.get(CONF_EV_MIN_CURRENT, DEFAULT_EV_MIN_CURRENT)
    max_current = user_input.get(CONF_EV_MAX_CURRENT, DEFAULT_EV_MAX_CURRENT)
    if min_current > max_current:
        errors[CONF_EV_MIN_CURRENT] = "min_above_max"
    cheap = user_input.get(CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE)
    expensive = user_input.get(
        CONF_EXPENSIVE_PRICE_PERCENTILE, DEFAULT_EXPENSIVE_PRICE_PERCENTILE
    )
    if cheap >= expensive:
        errors[CONF_CHEAP_PRICE_PERCENTILE] = "cheap_above_expensive"
    if int(user_input.get(CONF_EV_PHASES, DEFAULT_EV_PHASES)) not in VALID_EV_PHASES:
        errors[CONF_EV_PHASES] = "invalid_phases"
    voltage = user_input.get(CONF_EV_VOLTAGE, DEFAULT_EV_VOLTAGE)
    if not MIN_ALLOWED_EV_VOLTAGE <= voltage <= MAX_ALLOWED_EV_VOLTAGE:
        errors[CONF_EV_VOLTAGE] = "invalid_voltage"
    if not user_input.get(CONF_EV_ALLOWED_DAYS, list(WEEKDAYS)):
        errors[CONF_EV_ALLOWED_DAYS] = "no_days_selected"
    return errors


# ---------------------------------------------------------------------------
# Selector shorthands
# ---------------------------------------------------------------------------
def _sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _entity_selector(domains: list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))


def _options_selector(options: list[str], translation_key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=translation_key,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _number_selector(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=minimum,
        max=maximum,
        step=step,
        mode=selector.NumberSelectorMode.BOX,
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


# ---------------------------------------------------------------------------
# Step schemas
# ---------------------------------------------------------------------------
def _energy_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SOLAR_PRODUCTION_ENTITY): _sensor_selector(),
            vol.Required(CONF_HOUSE_CONSUMPTION_ENTITY): _sensor_selector(),
        }
    )


def _battery_enabled_schema() -> vol.Schema:
    return vol.Schema(
        {vol.Required(CONF_BATTERY_ENABLED, default=False): selector.BooleanSelector()}
    )


def _battery_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_BATTERY_SOC_ENTITY): _sensor_selector(),
            vol.Required(
                CONF_BATTERY_POWER_MODE, default=BatteryPowerMode.NONE.value
            ): _options_selector(
                [mode.value for mode in BatteryPowerMode], "battery_power_mode"
            ),
            vol.Optional(CONF_BATTERY_CHARGING_ENABLED_ENTITY): _entity_selector(
                _BATTERY_TOGGLE_DOMAINS
            ),
            vol.Optional(CONF_BATTERY_CHARGE_LIMIT_ENTITY): _entity_selector(
                _BATTERY_LIMIT_DOMAINS
            ),
        }
    )


def _battery_signed_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_BATTERY_POWER_ENTITY): _sensor_selector(),
            vol.Required(
                CONF_BATTERY_POWER_SIGN,
                default=BatteryPowerSign.POSITIVE_IS_CHARGING.value,
            ): _options_selector(
                [sign.value for sign in BatteryPowerSign], "battery_power_sign"
            ),
        }
    )


def _battery_separate_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_BATTERY_CHARGE_POWER_ENTITY): _sensor_selector(),
            vol.Required(CONF_BATTERY_DISCHARGE_POWER_ENTITY): _sensor_selector(),
        }
    )


def _ev_enabled_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_EV_CHARGER_ENABLED, default=False
            ): selector.BooleanSelector()
        }
    )


def _ev_control_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_EV_CONTROL_TYPE, default=EvControlType.SWITCH.value
            ): _options_selector(
                [control.value for control in EvControlType], "ev_control_type"
            ),
        }
    )


def _ev_switch_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EV_CHARGER_SWITCH_ENTITY): _entity_selector(
                _TOGGLE_DOMAINS
            ),
        }
    )


def _ev_start_stop_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EV_CHARGER_START_ENTITY): _entity_selector(
                _START_STOP_DOMAINS
            ),
            vol.Required(CONF_EV_CHARGER_STOP_ENTITY): _entity_selector(
                _START_STOP_DOMAINS
            ),
        }
    )


def _ev_details_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_EV_CHARGER_CURRENT_ENTITY): _entity_selector(
                _NUMBER_DOMAINS
            ),
            vol.Optional(CONF_EV_CABLE_CONNECTION_ENTITY): _entity_selector(
                ["binary_sensor", "sensor"]
            ),
            vol.Optional(
                CONF_EV_CONNECTED_STATES,
                default=list(DEFAULT_EV_CONNECTED_STATES),
            ): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
            vol.Optional(CONF_EV_SOC_ENTITY): _sensor_selector(),
            vol.Optional(CONF_EV_MIN_SOC_ENTITY): _entity_selector(
                ["sensor", "number", "input_number"]
            ),
        }
    )


def _price_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_ELECTRICITY_PRICE_ENTITY): _sensor_selector(),
            vol.Optional(CONF_GRID_EXPORT_SWITCH_ENTITY): _entity_selector(
                _TOGGLE_DOMAINS
            ),
        }
    )


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    def _default(key: str, fallback: Any) -> Any:
        return current.get(key, fallback)

    return vol.Schema(
        {
            vol.Required(
                CONF_EV_MIN_CURRENT, default=_default(CONF_EV_MIN_CURRENT, DEFAULT_EV_MIN_CURRENT)
            ): _number_selector(MIN_ALLOWED_EV_CURRENT, MAX_ALLOWED_EV_CURRENT, 0.5, "A"),
            vol.Required(
                CONF_EV_MAX_CURRENT, default=_default(CONF_EV_MAX_CURRENT, DEFAULT_EV_MAX_CURRENT)
            ): _number_selector(MIN_ALLOWED_EV_CURRENT, MAX_ALLOWED_EV_CURRENT, 0.5, "A"),
            vol.Required(
                CONF_EV_CURRENT_STEP,
                default=_default(CONF_EV_CURRENT_STEP, DEFAULT_EV_CURRENT_STEP),
            ): _number_selector(0.5, 8, 0.5, "A"),
            vol.Required(
                CONF_EV_PHASES, default=_default(CONF_EV_PHASES, DEFAULT_EV_PHASES)
            ): _number_selector(1, 3, 2),
            vol.Required(
                CONF_EV_VOLTAGE, default=_default(CONF_EV_VOLTAGE, DEFAULT_EV_VOLTAGE)
            ): _number_selector(MIN_ALLOWED_EV_VOLTAGE, MAX_ALLOWED_EV_VOLTAGE, 1, "V"),
            vol.Required(
                CONF_EV_START_DELAY, default=_default(CONF_EV_START_DELAY, DEFAULT_EV_START_DELAY)
            ): _number_selector(0, 3600, 10, "s"),
            vol.Required(
                CONF_EV_STOP_DELAY, default=_default(CONF_EV_STOP_DELAY, DEFAULT_EV_STOP_DELAY)
            ): _number_selector(0, 3600, 10, "s"),
            vol.Required(
                CONF_EV_ADJUSTMENT_INTERVAL,
                default=_default(CONF_EV_ADJUSTMENT_INTERVAL, DEFAULT_EV_ADJUSTMENT_INTERVAL),
            ): _number_selector(10, 3600, 10, "s"),
            vol.Required(
                CONF_EV_POWER_RESERVE,
                default=_default(CONF_EV_POWER_RESERVE, DEFAULT_EV_POWER_RESERVE),
            ): _number_selector(0, 5000, 50, "W"),
            vol.Required(
                CONF_BATTERY_RESERVE_SOC,
                default=_default(CONF_BATTERY_RESERVE_SOC, DEFAULT_BATTERY_RESERVE_SOC),
            ): _number_selector(0, 100, 1, "%"),
            vol.Required(
                CONF_BATTERY_TARGET_SOC,
                default=_default(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC),
            ): _number_selector(0, 100, 1, "%"),
            vol.Required(
                CONF_EV_TARGET_SOC, default=_default(CONF_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC)
            ): _number_selector(0, 100, 1, "%"),
            vol.Optional(
                CONF_EV_BATTERY_CAPACITY_KWH,
                description={
                    "suggested_value": current.get(CONF_EV_BATTERY_CAPACITY_KWH)
                },
            ): _number_selector(1, 300, 0.1, "kWh"),
            vol.Required(
                CONF_EV_CHARGING_EFFICIENCY,
                default=_default(CONF_EV_CHARGING_EFFICIENCY, DEFAULT_EV_CHARGING_EFFICIENCY),
            ): _number_selector(50, 100, 1, "%"),
            vol.Optional(
                CONF_EV_DEPARTURE_TIME,
                description={"suggested_value": current.get(CONF_EV_DEPARTURE_TIME)},
            ): selector.TimeSelector(),
            vol.Required(
                CONF_CHEAP_PRICE_PERCENTILE,
                default=_default(CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE),
            ): _number_selector(0, 100, 1),
            vol.Required(
                CONF_EXPENSIVE_PRICE_PERCENTILE,
                default=_default(
                    CONF_EXPENSIVE_PRICE_PERCENTILE, DEFAULT_EXPENSIVE_PRICE_PERCENTILE
                ),
            ): _number_selector(0, 100, 1),
            vol.Required(
                CONF_MANUAL_OVERRIDE_PAUSE,
                default=_default(CONF_MANUAL_OVERRIDE_PAUSE, DEFAULT_MANUAL_OVERRIDE_PAUSE),
            ): _number_selector(0, 1440, 1, "min"),
            vol.Required(
                CONF_MINIMUM_COMMAND_INTERVAL,
                default=_default(
                    CONF_MINIMUM_COMMAND_INTERVAL, DEFAULT_MINIMUM_COMMAND_INTERVAL
                ),
            ): _number_selector(5, 3600, 5, "s"),
            vol.Required(
                CONF_DATA_STALE_TIMEOUT,
                default=_default(CONF_DATA_STALE_TIMEOUT, DEFAULT_DATA_STALE_TIMEOUT),
            ): _number_selector(30, 86400, 30, "s"),
            vol.Required(
                CONF_EVALUATION_INTERVAL,
                default=_default(CONF_EVALUATION_INTERVAL, DEFAULT_EVALUATION_INTERVAL),
            ): _number_selector(10, 3600, 5, "s"),
            vol.Required(
                CONF_EV_ALLOWED_DAYS,
                default=_default(CONF_EV_ALLOWED_DAYS, list(WEEKDAYS)),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(WEEKDAYS),
                    translation_key="weekday",
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_EV_SCHEDULE_START,
                default=_default(CONF_EV_SCHEDULE_START, DEFAULT_EV_SCHEDULE_START),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_EV_SCHEDULE_END,
                default=_default(CONF_EV_SCHEDULE_END, DEFAULT_EV_SCHEDULE_END),
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_EXPORT_PRICE_THRESHOLD,
                description={
                    "suggested_value": current.get(CONF_EXPORT_PRICE_THRESHOLD)
                },
            ): _number_selector(-100, 100, 0.01),
        }
    )


class SolarBuddyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Multi-step setup flow. Also handles reconfiguration."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._reconfigure = False

    def _show(
        self, step_id: str, schema: vol.Schema, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Show a form pre-filled with already collected values."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors or {},
        )

    # -- Step 1: mandatory energy sensors ---------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect solar production and house consumption sensors."""
        errors: dict[str, str] = {}
        if user_input is not None:
            solar = user_input[CONF_SOLAR_PRODUCTION_ENTITY]
            house = user_input[CONF_HOUSE_CONSUMPTION_ENTITY]
            if solar == house:
                errors[CONF_HOUSE_CONSUMPTION_ENTITY] = "same_entity"
            if (error := _validate_power_entity(self.hass, solar)) is not None:
                errors[CONF_SOLAR_PRODUCTION_ENTITY] = error
            if (error := _validate_power_entity(self.hass, house)) is not None:
                errors[CONF_HOUSE_CONSUMPTION_ENTITY] = error
            if not errors:
                self._data.update(user_input)
                return await self.async_step_battery()
        return self._show("user", _energy_schema(), errors)

    # -- Battery -----------------------------------------------------------
    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether a house battery should be configured."""
        if user_input is not None:
            self._data[CONF_BATTERY_ENABLED] = user_input[CONF_BATTERY_ENABLED]
            if user_input[CONF_BATTERY_ENABLED]:
                return await self.async_step_battery_details()
            self._clear_battery_fields(keep_enabled=True)
            return await self.async_step_ev()
        return self._show("battery", _battery_enabled_schema())

    async def async_step_battery_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect battery SoC, power mode and optional control entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            soc = user_input.get(CONF_BATTERY_SOC_ENTITY)
            if soc and (error := _validate_percentage_entity(self.hass, soc)):
                errors[CONF_BATTERY_SOC_ENTITY] = error
            if not errors:
                self._replace_optional_fields(
                    user_input,
                    (
                        CONF_BATTERY_SOC_ENTITY,
                        CONF_BATTERY_CHARGING_ENABLED_ENTITY,
                        CONF_BATTERY_CHARGE_LIMIT_ENTITY,
                    ),
                )
                self._data[CONF_BATTERY_POWER_MODE] = user_input[
                    CONF_BATTERY_POWER_MODE
                ]
                mode = BatteryPowerMode(user_input[CONF_BATTERY_POWER_MODE])
                if mode is BatteryPowerMode.SIGNED:
                    self._drop_fields(
                        CONF_BATTERY_CHARGE_POWER_ENTITY,
                        CONF_BATTERY_DISCHARGE_POWER_ENTITY,
                    )
                    return await self.async_step_battery_signed()
                if mode is BatteryPowerMode.SEPARATE:
                    self._drop_fields(
                        CONF_BATTERY_POWER_ENTITY, CONF_BATTERY_POWER_SIGN
                    )
                    return await self.async_step_battery_separate()
                self._drop_fields(
                    CONF_BATTERY_POWER_ENTITY,
                    CONF_BATTERY_POWER_SIGN,
                    CONF_BATTERY_CHARGE_POWER_ENTITY,
                    CONF_BATTERY_DISCHARGE_POWER_ENTITY,
                )
                return await self.async_step_ev()
        return self._show("battery_details", _battery_schema(), errors)

    async def async_step_battery_signed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the signed battery power sensor and its sign convention."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_BATTERY_POWER_ENTITY]
            if (error := _validate_power_entity(self.hass, entity_id)) is not None:
                errors[CONF_BATTERY_POWER_ENTITY] = error
            if not errors:
                self._data.update(user_input)
                return await self.async_step_ev()
        return self._show("battery_signed", _battery_signed_schema(), errors)

    async def async_step_battery_separate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect separate charge and discharge power sensors."""
        errors: dict[str, str] = {}
        if user_input is not None:
            charge = user_input[CONF_BATTERY_CHARGE_POWER_ENTITY]
            discharge = user_input[CONF_BATTERY_DISCHARGE_POWER_ENTITY]
            if charge == discharge:
                errors[CONF_BATTERY_DISCHARGE_POWER_ENTITY] = "same_entity"
            if (error := _validate_power_entity(self.hass, charge)) is not None:
                errors[CONF_BATTERY_CHARGE_POWER_ENTITY] = error
            if (error := _validate_power_entity(self.hass, discharge)) is not None:
                errors[CONF_BATTERY_DISCHARGE_POWER_ENTITY] = error
            if not errors:
                self._data.update(user_input)
                return await self.async_step_ev()
        return self._show("battery_separate", _battery_separate_schema(), errors)

    # -- EV charger ---------------------------------------------------------
    async def async_step_ev(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether an EV charger should be configured."""
        if user_input is not None:
            self._data[CONF_EV_CHARGER_ENABLED] = user_input[CONF_EV_CHARGER_ENABLED]
            if user_input[CONF_EV_CHARGER_ENABLED]:
                return await self.async_step_ev_control()
            self._clear_ev_fields()
            return await self.async_step_price()
        return self._show("ev", _ev_enabled_schema())

    async def async_step_ev_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between a single switch or separate start/stop entities."""
        if user_input is not None:
            self._data[CONF_EV_CONTROL_TYPE] = user_input[CONF_EV_CONTROL_TYPE]
            if EvControlType(user_input[CONF_EV_CONTROL_TYPE]) is EvControlType.SWITCH:
                self._drop_fields(
                    CONF_EV_CHARGER_START_ENTITY, CONF_EV_CHARGER_STOP_ENTITY
                )
                return await self.async_step_ev_switch()
            self._drop_fields(CONF_EV_CHARGER_SWITCH_ENTITY)
            return await self.async_step_ev_start_stop()
        return self._show("ev_control", _ev_control_schema())

    async def async_step_ev_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the single on/off switch for the charger."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_ev_details()
        return self._show("ev_switch", _ev_switch_schema())

    async def async_step_ev_start_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect separate start and stop entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            start = user_input[CONF_EV_CHARGER_START_ENTITY]
            stop = user_input[CONF_EV_CHARGER_STOP_ENTITY]
            if start == stop:
                errors[CONF_EV_CHARGER_STOP_ENTITY] = "same_entity"
            if not errors:
                self._data.update(user_input)
                return await self.async_step_ev_details()
        return self._show("ev_start_stop", _ev_start_stop_schema(), errors)

    async def async_step_ev_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect charger current, cable status and EV SoC entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            for key in (CONF_EV_SOC_ENTITY, CONF_EV_MIN_SOC_ENTITY):
                entity_id = user_input.get(key)
                if entity_id and (
                    error := _validate_percentage_entity(self.hass, entity_id)
                ):
                    errors[key] = error
            if not errors:
                self._replace_optional_fields(
                    user_input,
                    (
                        CONF_EV_CHARGER_CURRENT_ENTITY,
                        CONF_EV_CABLE_CONNECTION_ENTITY,
                        CONF_EV_SOC_ENTITY,
                        CONF_EV_MIN_SOC_ENTITY,
                    ),
                )
                states = user_input.get(
                    CONF_EV_CONNECTED_STATES, list(DEFAULT_EV_CONNECTED_STATES)
                )
                # Comparison is case-insensitive and whitespace-tolerant.
                self._data[CONF_EV_CONNECTED_STATES] = [
                    str(item).strip().lower() for item in states if str(item).strip()
                ]
                return await self.async_step_price()
        return self._show("ev_details", _ev_details_schema(), errors)

    # -- Electricity price ---------------------------------------------------
    async def async_step_price(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the optional electricity price sensor and finish."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input.get(CONF_ELECTRICITY_PRICE_ENTITY)
            if entity_id and (error := _validate_price_entity(self.hass, entity_id)):
                errors[CONF_ELECTRICITY_PRICE_ENTITY] = error
            if not errors:
                self._replace_optional_fields(
                    user_input,
                    (CONF_ELECTRICITY_PRICE_ENTITY, CONF_GRID_EXPORT_SWITCH_ENTITY),
                )
                return self._finish()
        return self._show("price", _price_schema(), errors)

    def _finish(self) -> ConfigFlowResult:
        if self._reconfigure:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data=self._data
            )
        return self.async_create_entry(title="Solar Buddy", data=self._data)

    # -- Reconfigure ----------------------------------------------------------
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-run the entity mapping steps seeded with the current values."""
        self._reconfigure = True
        self._data = dict(self._get_reconfigure_entry().data)
        return await self.async_step_user()

    # -- Helpers ---------------------------------------------------------------
    def _replace_optional_fields(
        self, user_input: dict[str, Any], keys: tuple[str, ...]
    ) -> None:
        """Store provided optional fields; drop the ones the user cleared."""
        for key in keys:
            value = user_input.get(key)
            if value:
                self._data[key] = value
            else:
                self._data.pop(key, None)

    def _drop_fields(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)

    def _clear_battery_fields(self, *, keep_enabled: bool) -> None:
        self._drop_fields(
            CONF_BATTERY_SOC_ENTITY,
            CONF_BATTERY_POWER_MODE,
            CONF_BATTERY_POWER_ENTITY,
            CONF_BATTERY_POWER_SIGN,
            CONF_BATTERY_CHARGE_POWER_ENTITY,
            CONF_BATTERY_DISCHARGE_POWER_ENTITY,
            CONF_BATTERY_CHARGING_ENABLED_ENTITY,
            CONF_BATTERY_CHARGE_LIMIT_ENTITY,
        )
        if not keep_enabled:
            self._data.pop(CONF_BATTERY_ENABLED, None)

    def _clear_ev_fields(self) -> None:
        self._drop_fields(
            CONF_EV_CONTROL_TYPE,
            CONF_EV_CHARGER_SWITCH_ENTITY,
            CONF_EV_CHARGER_START_ENTITY,
            CONF_EV_CHARGER_STOP_ENTITY,
            CONF_EV_CHARGER_CURRENT_ENTITY,
            CONF_EV_CABLE_CONNECTION_ENTITY,
            CONF_EV_CONNECTED_STATES,
            CONF_EV_SOC_ENTITY,
            CONF_EV_MIN_SOC_ENTITY,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> SolarBuddyOptionsFlow:
        """Return the options flow."""
        return SolarBuddyOptionsFlow()


class SolarBuddyOptionsFlow(OptionsFlow):
    """Behavior settings: currents, delays, percentiles, timeouts."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and validate the combined options form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_EV_PHASES] = int(user_input[CONF_EV_PHASES])
            errors = validate_options(user_input)
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
            errors=errors,
        )
