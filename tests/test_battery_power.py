"""Battery power measurement through the coordinator (both modes)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.solar_buddy.const import (
    CONF_BATTERY_CHARGE_POWER_ENTITY,
    CONF_BATTERY_DISCHARGE_POWER_ENTITY,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_MODE,
    CONF_BATTERY_POWER_SIGN,
    CONF_EV_CHARGER_ENABLED,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    BatteryPowerMode,
    BatteryPowerSign,
)

from .conftest import make_entry, set_basic_states, set_power


async def setup_with(hass: HomeAssistant, extra: dict):
    data = {
        CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
        CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        CONF_BATTERY_ENABLED: True,
        CONF_EV_CHARGER_ENABLED: False,
        **extra,
    }
    entry = make_entry(data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(
    ("sign", "sensor_value", "expected_charge", "expected_discharge"),
    [
        (BatteryPowerSign.POSITIVE_IS_CHARGING, "2000", 2000.0, 0.0),
        (BatteryPowerSign.POSITIVE_IS_CHARGING, "-1500", 0.0, 1500.0),
        (BatteryPowerSign.POSITIVE_IS_DISCHARGING, "1500", 0.0, 1500.0),
        (BatteryPowerSign.POSITIVE_IS_DISCHARGING, "-2000", 2000.0, 0.0),
    ],
)
async def test_signed_battery_power(
    hass: HomeAssistant, sign, sensor_value, expected_charge, expected_discharge
) -> None:
    set_basic_states(hass)
    set_power(hass, "sensor.battery_power", sensor_value)
    entry = await setup_with(
        hass,
        {
            CONF_BATTERY_POWER_MODE: BatteryPowerMode.SIGNED.value,
            CONF_BATTERY_POWER_ENTITY: "sensor.battery_power",
            CONF_BATTERY_POWER_SIGN: sign.value,
        },
    )
    snapshot = entry.runtime_data.data.snapshot
    assert snapshot.battery_charge_power_w == expected_charge
    assert snapshot.battery_discharge_power_w == expected_discharge


async def test_signed_battery_power_in_kilowatts(hass: HomeAssistant) -> None:
    set_basic_states(hass)
    set_power(hass, "sensor.battery_power", "-1.5", unit="kW")
    entry = await setup_with(
        hass,
        {
            CONF_BATTERY_POWER_MODE: BatteryPowerMode.SIGNED.value,
            CONF_BATTERY_POWER_ENTITY: "sensor.battery_power",
            CONF_BATTERY_POWER_SIGN: BatteryPowerSign.POSITIVE_IS_CHARGING.value,
        },
    )
    snapshot = entry.runtime_data.data.snapshot
    assert snapshot.battery_discharge_power_w == 1500.0


async def test_separate_battery_sensors(hass: HomeAssistant) -> None:
    set_basic_states(hass)
    set_power(hass, "sensor.battery_charge", 1200)
    set_power(hass, "sensor.battery_discharge", -50)  # noise clamps to 0
    entry = await setup_with(
        hass,
        {
            CONF_BATTERY_POWER_MODE: BatteryPowerMode.SEPARATE.value,
            CONF_BATTERY_CHARGE_POWER_ENTITY: "sensor.battery_charge",
            CONF_BATTERY_DISCHARGE_POWER_ENTITY: "sensor.battery_discharge",
        },
    )
    snapshot = entry.runtime_data.data.snapshot
    assert snapshot.battery_charge_power_w == 1200.0
    assert snapshot.battery_discharge_power_w == 0.0


async def test_unavailable_battery_sensor_does_not_break_integration(
    hass: HomeAssistant,
) -> None:
    """A failing optional battery entity must not take Solar Buddy down."""
    set_basic_states(hass)
    hass.states.async_set("sensor.battery_power", "unavailable")
    entry = await setup_with(
        hass,
        {
            CONF_BATTERY_POWER_MODE: BatteryPowerMode.SIGNED.value,
            CONF_BATTERY_POWER_ENTITY: "sensor.battery_power",
            CONF_BATTERY_POWER_SIGN: BatteryPowerSign.POSITIVE_IS_CHARGING.value,
        },
    )
    data = entry.runtime_data.data
    assert data.snapshot.battery_charge_power_w == 0.0
    assert data.snapshot.battery_discharge_power_w == 0.0
    # Mandatory sensors are fine, so data is still ready.
    assert data.decision.data_ready is True
    assert any(issue.startswith("unavailable:") for issue in data.issues)
