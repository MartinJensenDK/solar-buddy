"""Shared fixtures for Solar Buddy tests."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_buddy.const import (
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_MODE,
    CONF_BATTERY_POWER_SIGN,
    CONF_BATTERY_SOC_ENTITY,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_EV_CABLE_CONNECTION_ENTITY,
    CONF_EV_CHARGER_CURRENT_ENTITY,
    CONF_EV_CHARGER_ENABLED,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CONNECTED_STATES,
    CONF_EV_CONTROL_TYPE,
    CONF_EV_SOC_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    DOMAIN,
    BatteryPowerMode,
    BatteryPowerSign,
    EvControlType,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""
    return


def set_power(hass, entity_id: str, value: float | str, unit: str = "W") -> None:
    """Set a power sensor state with a unit."""
    hass.states.async_set(entity_id, str(value), {"unit_of_measurement": unit})


def set_percent(hass, entity_id: str, value: float | str) -> None:
    """Set a percentage sensor state."""
    hass.states.async_set(entity_id, str(value), {"unit_of_measurement": "%"})


@pytest.fixture
def basic_config_data() -> dict[str, Any]:
    """Minimal valid configuration: only the two energy sensors."""
    return {
        CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
        CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        CONF_BATTERY_ENABLED: False,
        CONF_EV_CHARGER_ENABLED: False,
    }


@pytest.fixture
def full_config_data() -> dict[str, Any]:
    """Configuration with battery (signed), EV (switch) and price sensor."""
    return {
        CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
        CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        CONF_BATTERY_ENABLED: True,
        CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
        CONF_BATTERY_POWER_MODE: BatteryPowerMode.SIGNED.value,
        CONF_BATTERY_POWER_ENTITY: "sensor.battery_power",
        CONF_BATTERY_POWER_SIGN: BatteryPowerSign.POSITIVE_IS_CHARGING.value,
        CONF_EV_CHARGER_ENABLED: True,
        CONF_EV_CONTROL_TYPE: EvControlType.SWITCH.value,
        CONF_EV_CHARGER_SWITCH_ENTITY: "switch.ev_charger",
        CONF_EV_CHARGER_CURRENT_ENTITY: "number.ev_current",
        CONF_EV_CABLE_CONNECTION_ENTITY: "binary_sensor.ev_cable",
        CONF_EV_CONNECTED_STATES: ["connected", "charging"],
        CONF_EV_SOC_ENTITY: "sensor.ev_soc",
        CONF_ELECTRICITY_PRICE_ENTITY: "sensor.electricity_price",
    }


def set_basic_states(hass, solar: float = 3000.0, house: float = 800.0) -> None:
    """Set valid states for the two mandatory sensors."""
    set_power(hass, "sensor.solar_production", solar)
    set_power(hass, "sensor.house_consumption", house)


def set_full_states(hass) -> None:
    """Set valid states for the full configuration."""
    set_basic_states(hass)
    set_percent(hass, "sensor.battery_soc", 55)
    set_power(hass, "sensor.battery_power", 500)
    hass.states.async_set("switch.ev_charger", "off")
    hass.states.async_set("number.ev_current", "6")
    hass.states.async_set("binary_sensor.ev_cable", "on")
    set_percent(hass, "sensor.ev_soc", 40)
    hass.states.async_set(
        "sensor.electricity_price",
        "1.25",
        {"currency": "DKK", "unit": "kWh", "raw_today": [], "tomorrow_valid": False},
    )


def make_entry(data: dict[str, Any], options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Create a Solar Buddy MockConfigEntry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Solar Buddy",
        data=data,
        options=options or {},
        version=1,
        minor_version=1,
    )
