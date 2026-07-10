"""Entity behavior tests: values, defaults, and runtime toggles."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.solar_buddy.const import (
    CONF_GRID_EXPORT_SWITCH_ENTITY,
    DOMAIN,
    Priority,
    Strategy,
)

from .conftest import make_entry, set_basic_states, set_full_states


def entity_id_for(hass: HomeAssistant, platform: str, entry, key: str) -> str:
    """Resolve an entity id from its stable unique id."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, f"entity for {key} not registered"
    return entity_id


async def setup_full(hass: HomeAssistant, full_config_data):
    set_full_states(hass)
    entry = make_entry(full_config_data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensor_values(hass: HomeAssistant, full_config_data) -> None:
    entry = await setup_full(hass, full_config_data)

    surplus = hass.states.get(entity_id_for(hass, "sensor", entry, "solar_surplus"))
    assert float(surplus.state) == 2200.0  # 3000 - 800

    status = hass.states.get(entity_id_for(hass, "sensor", entry, "status"))
    assert status.state == "monitoring"

    price = hass.states.get(entity_id_for(hass, "sensor", entry, "current_price"))
    assert float(price.state) == 1.25
    assert price.attributes["unit_of_measurement"] == "kWh"
    assert price.attributes["currency"] == "DKK"

    ev_connected = hass.states.get(
        entity_id_for(hass, "binary_sensor", entry, "ev_connected")
    )
    assert ev_connected.state == "on"

    data_ready = hass.states.get(
        entity_id_for(hass, "binary_sensor", entry, "data_ready")
    )
    assert data_ready.state == "on"


async def test_automatic_control_defaults_off(
    hass: HomeAssistant, full_config_data
) -> None:
    """Solar Buddy must never enable itself."""
    entry = await setup_full(hass, full_config_data)
    switch_id = entity_id_for(hass, "switch", entry, "automatic_control")
    assert hass.states.get(switch_id).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    assert hass.states.get(switch_id).state == "on"
    assert entry.runtime_data.automatic_control is True

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_id}, blocking=True
    )
    assert entry.runtime_data.automatic_control is False


async def test_strategy_select(hass: HomeAssistant, full_config_data) -> None:
    entry = await setup_full(hass, full_config_data)
    select_id = entity_id_for(hass, "select", entry, "strategy")
    state = hass.states.get(select_id)
    assert state.state == Strategy.MONITOR_ONLY.value  # default
    assert set(state.attributes["options"]) == {s.value for s in Strategy}

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": Strategy.SOLAR_ONLY.value},
        blocking=True,
    )
    assert entry.runtime_data.strategy is Strategy.SOLAR_ONLY
    assert hass.states.get(select_id).state == Strategy.SOLAR_ONLY.value


async def test_priority_select(hass: HomeAssistant, full_config_data) -> None:
    entry = await setup_full(hass, full_config_data)
    select_id = entity_id_for(hass, "select", entry, "priority")
    assert hass.states.get(select_id).state == Priority.BATTERY_FIRST.value

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": Priority.EV_FIRST.value},
        blocking=True,
    )
    assert entry.runtime_data.priority is Priority.EV_FIRST


async def test_recalculate_button(hass: HomeAssistant, full_config_data) -> None:
    entry = await setup_full(hass, full_config_data)
    button_id = entity_id_for(hass, "button", entry, "recalculate")
    await hass.services.async_call(
        "button", "press", {"entity_id": button_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.data is not None


async def test_all_expected_entities_exist(
    hass: HomeAssistant, full_config_data
) -> None:
    entry = await setup_full(hass, full_config_data)
    expected = {
        "sensor": [
            "status",
            "recommendation",
            "solar_surplus",
            "available_ev_power",
            "recommended_ev_current",
            "current_price",
            "price_level",
            "next_action",
            "last_evaluation",
            "last_command",
        ],
        "binary_sensor": [
            "data_ready",
            "solar_surplus_available",
            "ev_connected",
            "automatic_control_available",
            "manual_override",
        ],
        "switch": ["automatic_control"],
        "select": ["strategy", "priority"],
        "button": ["recalculate", "clear_manual_override"],
    }
    registry = er.async_get(hass)
    for platform, keys in expected.items():
        for key in keys:
            unique_id = f"{entry.entry_id}_{key}"
            assert registry.async_get_entity_id(platform, DOMAIN, unique_id), (
                f"missing {platform}.{key}"
            )


async def test_battery_sensors_exist_with_battery(
    hass: HomeAssistant, full_config_data
) -> None:
    entry = await setup_full(hass, full_config_data)

    soc = hass.states.get(entity_id_for(hass, "sensor", entry, "battery_soc"))
    assert float(soc.state) == 55.0

    charge = hass.states.get(
        entity_id_for(hass, "sensor", entry, "battery_charge_power")
    )
    assert float(charge.state) == 500.0  # positive_is_charging, +500 W

    discharge = hass.states.get(
        entity_id_for(hass, "sensor", entry, "battery_discharge_power")
    )
    assert float(discharge.state) == 0.0


async def test_battery_sensors_absent_without_battery(
    hass: HomeAssistant, basic_config_data
) -> None:
    set_basic_states(hass)
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    for key in ("battery_soc", "battery_charge_power", "battery_discharge_power"):
        unique_id = f"{entry.entry_id}_{key}"
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None


async def test_schedule_entities(hass: HomeAssistant, full_config_data) -> None:
    entry = await setup_full(hass, full_config_data)
    coordinator = entry.runtime_data

    # All seven day switches exist and default to on.
    monday = entity_id_for(hass, "switch", entry, "charge_allowed_mon")
    assert hass.states.get(monday).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": monday}, blocking=True
    )
    assert "mon" not in coordinator.ev_allowed_days
    assert hass.states.get(monday).state == "off"

    start_id = entity_id_for(hass, "time", entry, "ev_schedule_start")
    await hass.services.async_call(
        "time", "set_value", {"entity_id": start_id, "time": "10:00:00"}, blocking=True
    )
    assert coordinator.ev_schedule_start == "10:00:00"
    assert hass.states.get(start_id).state == "10:00:00"


async def test_export_threshold_number(hass: HomeAssistant, full_config_data) -> None:
    full_config_data[CONF_GRID_EXPORT_SWITCH_ENTITY] = "switch.grid_export"
    hass.states.async_set("switch.grid_export", "on")
    entry = await setup_full(hass, full_config_data)

    number_id = entity_id_for(hass, "number", entry, "export_price_threshold")
    assert float(hass.states.get(number_id).state) == 0.0

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 0.25}, blocking=True
    )
    assert entry.runtime_data.export_price_threshold == 0.25


async def test_no_schedule_entities_without_ev(
    hass: HomeAssistant, basic_config_data
) -> None:
    set_basic_states(hass)
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_charge_allowed_mon"
    ) is None
    assert registry.async_get_entity_id(
        "time", DOMAIN, f"{entry.entry_id}_ev_schedule_start"
    ) is None
    assert registry.async_get_entity_id(
        "number", DOMAIN, f"{entry.entry_id}_export_price_threshold"
    ) is None
