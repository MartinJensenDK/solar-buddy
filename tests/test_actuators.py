"""Tests for the actuator layer."""

from __future__ import annotations

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.solar_buddy.actuators import (
    ActuatorAdapter,
    read_write_capability,
)
from custom_components.solar_buddy.const import ActuatorCapability, CommandAction
from custom_components.solar_buddy.models import ActuatorCommand


@pytest.fixture
def adapter(hass: HomeAssistant) -> ActuatorAdapter:
    return ActuatorAdapter(hass, "test-entry-id")


async def test_switch_on_off(hass: HomeAssistant, adapter: ActuatorAdapter) -> None:
    hass.states.async_set("switch.charger", "off")
    on_calls = async_mock_service(hass, "switch", "turn_on")
    off_calls = async_mock_service(hass, "switch", "turn_off")

    await adapter.turn_entity_on("switch.charger")
    await adapter.turn_entity_off("switch.charger")

    assert len(on_calls) == 1
    assert on_calls[0].data["entity_id"] == "switch.charger"
    assert len(off_calls) == 1


async def test_input_boolean_on_off(
    hass: HomeAssistant, adapter: ActuatorAdapter
) -> None:
    calls = async_mock_service(hass, "input_boolean", "turn_on")
    await adapter.turn_entity_on("input_boolean.charging")
    assert len(calls) == 1


async def test_button_press(hass: HomeAssistant, adapter: ActuatorAdapter) -> None:
    button_calls = async_mock_service(hass, "button", "press")
    input_calls = async_mock_service(hass, "input_button", "press")
    await adapter.press_entity("button.start")
    await adapter.press_entity("input_button.start")
    assert len(button_calls) == 1
    assert len(input_calls) == 1


async def test_script_press_runs_script(
    hass: HomeAssistant, adapter: ActuatorAdapter
) -> None:
    calls = async_mock_service(hass, "script", "turn_on")
    await adapter.press_entity("script.start_charging")
    assert len(calls) == 1


async def test_set_numeric_value(hass: HomeAssistant, adapter: ActuatorAdapter) -> None:
    number_calls = async_mock_service(hass, "number", "set_value")
    input_calls = async_mock_service(hass, "input_number", "set_value")

    await adapter.set_numeric_value("number.current", 10.0)
    await adapter.set_numeric_value("input_number.current", 8.0)

    assert number_calls[0].data == {"entity_id": "number.current", "value": 10.0}
    assert input_calls[0].data == {"entity_id": "input_number.current", "value": 8.0}


async def test_select_option(hass: HomeAssistant, adapter: ActuatorAdapter) -> None:
    calls = async_mock_service(hass, "select", "select_option")
    await adapter.set_select_option("select.charge_limit", "80")
    assert calls[0].data == {"entity_id": "select.charge_limit", "option": "80"}


async def test_unsupported_domains_raise(
    hass: HomeAssistant, adapter: ActuatorAdapter
) -> None:
    with pytest.raises(HomeAssistantError):
        await adapter.turn_entity_on("sensor.read_only")
    with pytest.raises(HomeAssistantError):
        await adapter.press_entity("switch.charger")
    with pytest.raises(HomeAssistantError):
        await adapter.set_numeric_value("switch.charger", 5.0)
    with pytest.raises(HomeAssistantError):
        await adapter.set_select_option("number.current", "x")


async def test_execute_command_paths(
    hass: HomeAssistant, adapter: ActuatorAdapter
) -> None:
    on_calls = async_mock_service(hass, "switch", "turn_on")
    value_calls = async_mock_service(hass, "number", "set_value")

    await adapter.execute(ActuatorCommand("switch.charger", CommandAction.TURN_ON))
    await adapter.execute(
        ActuatorCommand("number.current", CommandAction.SET_VALUE, 12.0)
    )
    assert len(on_calls) == 1
    assert len(value_calls) == 1

    with pytest.raises(HomeAssistantError):
        await adapter.execute(
            ActuatorCommand("number.current", CommandAction.SET_VALUE, "not a number")
        )
    with pytest.raises(HomeAssistantError):
        await adapter.execute(
            ActuatorCommand("select.mode", CommandAction.SELECT_OPTION, 5.0)
        )


async def test_service_error_propagates(
    hass: HomeAssistant, adapter: ActuatorAdapter
) -> None:
    """A missing service (unloaded integration) raises; callers handle it."""
    with pytest.raises(Exception):  # noqa: B017 - ServiceNotFound derives from HomeAssistantError
        await adapter.turn_entity_on("switch.not_backed_by_service")


async def test_own_context_is_recognized(
    hass: HomeAssistant, adapter: ActuatorAdapter
) -> None:
    context = adapter.new_context()
    assert adapter.is_own_context(context)
    assert not adapter.is_own_context(None)
    assert not adapter.is_own_context(Context())


async def test_read_write_capability(hass: HomeAssistant) -> None:
    hass.states.async_set("switch.a", "off")
    hass.states.async_set("input_boolean.b", "off")
    hass.states.async_set("button.c", "unknown")
    hass.states.async_set("script.d", "off")
    hass.states.async_set("number.e", "5")
    hass.states.async_set("select.f", "x")
    hass.states.async_set("sensor.g", "42")
    hass.states.async_set("binary_sensor.h", "on")

    assert read_write_capability(hass, "switch.a") is ActuatorCapability.TOGGLE
    assert read_write_capability(hass, "input_boolean.b") is ActuatorCapability.TOGGLE
    assert read_write_capability(hass, "button.c") is ActuatorCapability.PRESS
    assert read_write_capability(hass, "script.d") is ActuatorCapability.TOGGLE
    assert read_write_capability(hass, "number.e") is ActuatorCapability.SET_VALUE
    assert read_write_capability(hass, "select.f") is ActuatorCapability.SELECT
    assert read_write_capability(hass, "sensor.g") is ActuatorCapability.READ_ONLY
    assert read_write_capability(hass, "binary_sensor.h") is ActuatorCapability.READ_ONLY
    assert read_write_capability(hass, "switch.missing") is ActuatorCapability.MISSING
