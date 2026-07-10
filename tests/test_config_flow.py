"""Tests for the Solar Buddy config and reconfigure flows."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.solar_buddy.const import (
    CONF_BATTERY_CHARGE_POWER_ENTITY,
    CONF_BATTERY_DISCHARGE_POWER_ENTITY,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_MODE,
    CONF_BATTERY_POWER_SIGN,
    CONF_EV_CHARGER_ENABLED,
    CONF_EV_CHARGER_START_ENTITY,
    CONF_EV_CHARGER_STOP_ENTITY,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CONNECTED_STATES,
    CONF_EV_CONTROL_TYPE,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    DOMAIN,
    BatteryPowerMode,
    BatteryPowerSign,
    EvControlType,
)

from .conftest import make_entry, set_basic_states, set_power

ENERGY_INPUT = {
    CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
    CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
}


async def start_flow(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )


async def test_minimal_happy_path(hass: HomeAssistant) -> None:
    """Solar + house only; battery and EV disabled."""
    set_basic_states(hass)
    result = await start_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    assert result["step_id"] == "battery"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BATTERY_ENABLED: False}
    )
    assert result["step_id"] == "ev"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CHARGER_ENABLED: False}
    )
    assert result["step_id"] == "price"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solar Buddy"
    data = result["data"]
    assert data[CONF_SOLAR_PRODUCTION_ENTITY] == "sensor.solar_production"
    assert data[CONF_BATTERY_ENABLED] is False
    assert data[CONF_EV_CHARGER_ENABLED] is False


async def test_same_entity_rejected(hass: HomeAssistant) -> None:
    set_basic_states(hass)
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.solar_production",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_HOUSE_CONSUMPTION_ENTITY] == "same_entity"

    # Recovery: submitting valid input afterwards continues the flow.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    assert result["step_id"] == "battery"


async def test_energy_sensor_rejected(hass: HomeAssistant) -> None:
    set_power(hass, "sensor.solar_production", 5.0, unit="kWh")
    set_power(hass, "sensor.house_consumption", 800)
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    assert result["errors"][CONF_SOLAR_PRODUCTION_ENTITY] == "energy_sensor_selected"


async def test_wrong_unit_rejected(hass: HomeAssistant) -> None:
    set_power(hass, "sensor.solar_production", 3000)
    set_power(hass, "sensor.house_consumption", 21.5, unit="°C")
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    assert result["errors"][CONF_HOUSE_CONSUMPTION_ENTITY] == "not_a_power_sensor"


async def test_unavailable_entity_is_accepted(hass: HomeAssistant) -> None:
    """A temporarily unavailable entity must not block setup."""
    hass.states.async_set("sensor.solar_production", "unavailable")
    set_power(hass, "sensor.house_consumption", 800)
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    assert result["step_id"] == "battery"


async def test_battery_signed_path(hass: HomeAssistant) -> None:
    set_basic_states(hass)
    set_power(hass, "sensor.battery_power", -500)
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BATTERY_ENABLED: True}
    )
    assert result["step_id"] == "battery_details"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BATTERY_POWER_MODE: BatteryPowerMode.SIGNED.value},
    )
    assert result["step_id"] == "battery_signed"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BATTERY_POWER_ENTITY: "sensor.battery_power",
            CONF_BATTERY_POWER_SIGN: BatteryPowerSign.POSITIVE_IS_DISCHARGING.value,
        },
    )
    assert result["step_id"] == "ev"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CHARGER_ENABLED: False}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_BATTERY_POWER_ENTITY] == "sensor.battery_power"
    assert (
        data[CONF_BATTERY_POWER_SIGN]
        == BatteryPowerSign.POSITIVE_IS_DISCHARGING.value
    )
    # The signed path must not leave separate-sensor keys behind.
    assert CONF_BATTERY_CHARGE_POWER_ENTITY not in data


async def test_battery_separate_path_rejects_same_entity(hass: HomeAssistant) -> None:
    set_basic_states(hass)
    set_power(hass, "sensor.battery_charge", 500)
    set_power(hass, "sensor.battery_discharge", 0)
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BATTERY_ENABLED: True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BATTERY_POWER_MODE: BatteryPowerMode.SEPARATE.value},
    )
    assert result["step_id"] == "battery_separate"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BATTERY_CHARGE_POWER_ENTITY: "sensor.battery_charge",
            CONF_BATTERY_DISCHARGE_POWER_ENTITY: "sensor.battery_charge",
        },
    )
    assert result["errors"][CONF_BATTERY_DISCHARGE_POWER_ENTITY] == "same_entity"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BATTERY_CHARGE_POWER_ENTITY: "sensor.battery_charge",
            CONF_BATTERY_DISCHARGE_POWER_ENTITY: "sensor.battery_discharge",
        },
    )
    assert result["step_id"] == "ev"


async def test_ev_switch_path(hass: HomeAssistant) -> None:
    set_basic_states(hass)
    hass.states.async_set("switch.ev_charger", "off")
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BATTERY_ENABLED: False}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CHARGER_ENABLED: True}
    )
    assert result["step_id"] == "ev_control"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CONTROL_TYPE: EvControlType.SWITCH.value}
    )
    assert result["step_id"] == "ev_switch"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CHARGER_SWITCH_ENTITY: "switch.ev_charger"}
    )
    assert result["step_id"] == "ev_details"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EV_CONNECTED_STATES: ["Connected ", "CHARGING"]},
    )
    assert result["step_id"] == "price"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_EV_CHARGER_SWITCH_ENTITY] == "switch.ev_charger"
    # States are normalized: lowercased and stripped.
    assert data[CONF_EV_CONNECTED_STATES] == ["connected", "charging"]


async def test_ev_start_stop_path(hass: HomeAssistant) -> None:
    set_basic_states(hass)
    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BATTERY_ENABLED: False}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CHARGER_ENABLED: True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CONTROL_TYPE: EvControlType.START_STOP.value}
    )
    assert result["step_id"] == "ev_start_stop"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EV_CHARGER_START_ENTITY: "button.start",
            CONF_EV_CHARGER_STOP_ENTITY: "button.start",
        },
    )
    assert result["errors"][CONF_EV_CHARGER_STOP_ENTITY] == "same_entity"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EV_CHARGER_START_ENTITY: "button.start",
            CONF_EV_CHARGER_STOP_ENTITY: "button.stop",
        },
    )
    assert result["step_id"] == "ev_details"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_EV_CHARGER_START_ENTITY] == "button.start"
    assert data[CONF_EV_CHARGER_STOP_ENTITY] == "button.stop"
    assert CONF_EV_CHARGER_SWITCH_ENTITY not in data


async def test_second_entry_aborts(hass: HomeAssistant, basic_config_data) -> None:
    """single_config_entry only allows one Solar Buddy instance."""
    make_entry(basic_config_data).add_to_hass(hass)
    result = await start_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_flow(hass: HomeAssistant, basic_config_data) -> None:
    """Reconfiguring re-runs the steps and updates the entry in place."""
    set_basic_states(hass)
    set_power(hass, "sensor.new_solar", 2000)
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_PRODUCTION_ENTITY: "sensor.new_solar",
            CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BATTERY_ENABLED: False}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EV_CHARGER_ENABLED: False}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_SOLAR_PRODUCTION_ENTITY] == "sensor.new_solar"
