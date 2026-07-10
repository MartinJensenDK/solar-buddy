"""Tests for the Solar Buddy options flow."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.solar_buddy.const import (
    CONF_BATTERY_RESERVE_SOC,
    CONF_BATTERY_TARGET_SOC,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DATA_STALE_TIMEOUT,
    CONF_EV_ADJUSTMENT_INTERVAL,
    CONF_EV_CHARGING_EFFICIENCY,
    CONF_EV_CURRENT_STEP,
    CONF_EV_MAX_CURRENT,
    CONF_EV_MIN_CURRENT,
    CONF_EV_PHASES,
    CONF_EV_POWER_RESERVE,
    CONF_EV_START_DELAY,
    CONF_EV_STOP_DELAY,
    CONF_EV_TARGET_SOC,
    CONF_EV_VOLTAGE,
    CONF_EVALUATION_INTERVAL,
    CONF_EXPENSIVE_PRICE_PERCENTILE,
    CONF_MANUAL_OVERRIDE_PAUSE,
    CONF_MINIMUM_COMMAND_INTERVAL,
)

from .conftest import make_entry

VALID_OPTIONS = {
    CONF_EV_MIN_CURRENT: 6.0,
    CONF_EV_MAX_CURRENT: 16.0,
    CONF_EV_CURRENT_STEP: 1.0,
    CONF_EV_PHASES: 3,
    CONF_EV_VOLTAGE: 230.0,
    CONF_EV_START_DELAY: 60,
    CONF_EV_STOP_DELAY: 120,
    CONF_EV_ADJUSTMENT_INTERVAL: 60,
    CONF_EV_POWER_RESERVE: 200.0,
    CONF_BATTERY_RESERVE_SOC: 20.0,
    CONF_BATTERY_TARGET_SOC: 100.0,
    CONF_EV_TARGET_SOC: 80.0,
    CONF_EV_CHARGING_EFFICIENCY: 90.0,
    CONF_CHEAP_PRICE_PERCENTILE: 25.0,
    CONF_EXPENSIVE_PRICE_PERCENTILE: 75.0,
    CONF_MANUAL_OVERRIDE_PAUSE: 15,
    CONF_MINIMUM_COMMAND_INTERVAL: 60,
    CONF_DATA_STALE_TIMEOUT: 300,
    CONF_EVALUATION_INTERVAL: 30,
}


async def test_options_flow_valid(hass: HomeAssistant, basic_config_data) -> None:
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], VALID_OPTIONS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EV_MAX_CURRENT] == 16.0
    assert entry.options[CONF_EV_PHASES] == 3


async def test_options_flow_min_above_max(hass: HomeAssistant, basic_config_data) -> None:
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**VALID_OPTIONS, CONF_EV_MIN_CURRENT: 20.0, CONF_EV_MAX_CURRENT: 10.0},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_EV_MIN_CURRENT] == "min_above_max"


async def test_options_flow_percentile_order(
    hass: HomeAssistant, basic_config_data
) -> None:
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **VALID_OPTIONS,
            CONF_CHEAP_PRICE_PERCENTILE: 80.0,
            CONF_EXPENSIVE_PRICE_PERCENTILE: 75.0,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_CHEAP_PRICE_PERCENTILE] == "cheap_above_expensive"


async def test_options_flow_invalid_phases(
    hass: HomeAssistant, basic_config_data
) -> None:
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**VALID_OPTIONS, CONF_EV_PHASES: 2}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_EV_PHASES] == "invalid_phases"

    # Recovery after a validation error must work.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], VALID_OPTIONS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
