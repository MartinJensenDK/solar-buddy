"""Tests for battery decisions (optimizer) and the BatteryController."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.solar_buddy.battery_control import BatteryController
from custom_components.solar_buddy.const import (
    CONF_BATTERY_CHARGE_LIMIT_ENTITY,
    CONF_BATTERY_CHARGING_ENABLED_ENTITY,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_POWER_MODE,
    CONF_BATTERY_SOC_ENTITY,
    CONF_EV_CHARGER_ENABLED,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    BatteryPowerMode,
    Priority,
    Recommendation,
    SolarBuddyStatus,
    Strategy,
)
from custom_components.solar_buddy.models import (
    OptimizationDecision,
    OptimizationSettings,
)
from custom_components.solar_buddy.optimizer import evaluate

from .conftest import make_entry, set_basic_states, set_percent
from .test_optimizer import settings as optimizer_settings
from .test_optimizer import snapshot

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Optimizer: battery decision rules
# ---------------------------------------------------------------------------
def battery_settings(**overrides) -> OptimizationSettings:
    defaults = {
        "battery_configured": True,
        "battery_reserve_soc": 20.0,
        "battery_target_soc": 90.0,
    }
    defaults.update(overrides)
    return optimizer_settings(**defaults)


def test_battery_charging_off_at_target() -> None:
    decision = evaluate(
        snapshot(battery_soc=90.0), [], battery_settings(), data_ready=True
    )
    assert decision.should_enable_battery_charging is False
    assert decision.battery_charge_limit_pct == 90.0


def test_battery_charging_on_below_reserve() -> None:
    """Below the reserve the battery charges even with ev_first priority."""
    decision = evaluate(
        snapshot(battery_soc=10.0, ev_charging=True),
        [],
        battery_settings(priority=Priority.EV_FIRST),
        data_ready=True,
    )
    assert decision.should_enable_battery_charging is True


def test_ev_first_disables_battery_while_ev_charges() -> None:
    decision = evaluate(
        snapshot(battery_soc=50.0, ev_charging=True),
        [],
        battery_settings(priority=Priority.EV_FIRST),
        data_ready=True,
    )
    assert decision.should_enable_battery_charging is False


def test_battery_first_keeps_charging_while_ev_charges() -> None:
    decision = evaluate(
        snapshot(battery_soc=50.0, ev_charging=True),
        [],
        battery_settings(priority=Priority.BATTERY_FIRST),
        data_ready=True,
    )
    assert decision.should_enable_battery_charging is True


def test_unknown_soc_leaves_toggle_untouched() -> None:
    decision = evaluate(
        snapshot(battery_soc=None), [], battery_settings(), data_ready=True
    )
    assert decision.should_enable_battery_charging is None
    assert decision.battery_charge_limit_pct == 90.0  # limit is still safe


def test_no_battery_configured_no_battery_decision() -> None:
    decision = evaluate(
        snapshot(battery_soc=50.0),
        [],
        optimizer_settings(battery_configured=False),
        data_ready=True,
    )
    assert decision.should_enable_battery_charging is None
    assert decision.battery_charge_limit_pct is None


def test_no_battery_decision_when_data_not_ready() -> None:
    decision = evaluate(
        snapshot(battery_soc=50.0), [], battery_settings(), data_ready=False
    )
    assert decision.should_enable_battery_charging is None
    assert decision.battery_charge_limit_pct is None


# ---------------------------------------------------------------------------
# BatteryController (pure, with fake adapter)
# ---------------------------------------------------------------------------
class FakeAdapter:
    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self.fail = False

    async def _record(self, *command) -> None:
        if self.fail:
            raise HomeAssistantError("service failed")
        self.commands.append(command)

    async def turn_entity_on(self, entity_id: str) -> None:
        await self._record("on", entity_id)

    async def turn_entity_off(self, entity_id: str) -> None:
        await self._record("off", entity_id)

    async def press_entity(self, entity_id: str) -> None:
        await self._record("press", entity_id)

    async def set_numeric_value(self, entity_id: str, value: float) -> None:
        await self._record("set", entity_id, value)

    async def set_select_option(self, entity_id: str, option: str) -> None:
        await self._record("select", entity_id, option)


def make_controller(
    charging: str | None = "switch.battery_charging",
    limit: str | None = "number.battery_limit",
    states: dict[str, str] | None = None,
    options: dict[str, list[str]] | None = None,
) -> tuple[BatteryController, FakeAdapter, dict[str, str]]:
    adapter = FakeAdapter()
    state_map = states if states is not None else {}
    option_map = options or {}
    controller = BatteryController(
        adapter, charging, limit, state_map.get, option_map.get
    )
    return controller, adapter, state_map


def control_settings(**overrides) -> OptimizationSettings:
    defaults = {"minimum_command_interval_s": 60}
    defaults.update(overrides)
    return OptimizationSettings(**defaults)


def decision(**overrides) -> OptimizationDecision:
    defaults = {
        "strategy": Strategy.SOLAR_ONLY,
        "status": SolarBuddyStatus.ACTIVE,
        "recommendation": Recommendation.NO_EV_CONFIGURED,
        "data_ready": True,
    }
    defaults.update(overrides)
    return OptimizationDecision(**defaults)


async def test_toggle_and_limit_are_written() -> None:
    controller, adapter, _states = make_controller(
        states={"switch.battery_charging": "off", "number.battery_limit": "100"}
    )
    result = await controller.apply(
        decision(should_enable_battery_charging=True, battery_charge_limit_pct=90.0),
        control_settings(),
        NOW,
    )
    assert result
    assert adapter.commands == [
        ("on", "switch.battery_charging"),
        ("set", "number.battery_limit", 90.0),
    ]


async def test_commands_are_deduplicated() -> None:
    controller, adapter, _ = make_controller(
        states={"switch.battery_charging": "on", "number.battery_limit": "90.2"}
    )
    result = await controller.apply(
        decision(should_enable_battery_charging=True, battery_charge_limit_pct=90.0),
        control_settings(),
        NOW,
    )
    assert not result
    assert adapter.commands == []


async def test_minimum_command_interval_paces_commands() -> None:
    controller, _adapter, states = make_controller(
        states={"switch.battery_charging": "off", "number.battery_limit": "90"}
    )
    await controller.apply(
        decision(should_enable_battery_charging=True), control_settings(), NOW
    )
    states["switch.battery_charging"] = "on"

    off = decision(should_enable_battery_charging=False)
    result = await controller.apply(
        off, control_settings(), NOW + timedelta(seconds=30)
    )
    assert not result
    result = await controller.apply(
        off, control_settings(), NOW + timedelta(seconds=61)
    )
    assert result


async def test_read_only_entities_are_never_written() -> None:
    controller, adapter, _ = make_controller(
        charging="binary_sensor.battery_charging",
        limit="sensor.battery_limit",
        states={"binary_sensor.battery_charging": "off", "sensor.battery_limit": "80"},
    )
    assert not controller.charging_writable
    assert not controller.limit_writable
    assert controller.controlled_entity_ids() == set()
    result = await controller.apply(
        decision(should_enable_battery_charging=True, battery_charge_limit_pct=90.0),
        control_settings(),
        NOW,
    )
    assert not result
    assert adapter.commands == []


async def test_select_limit_picks_closest_option() -> None:
    controller, adapter, _ = make_controller(
        limit="select.battery_limit",
        states={"select.battery_limit": "100"},
        options={"select.battery_limit": ["50", "80", "100"]},
    )
    result = await controller.apply(
        decision(battery_charge_limit_pct=90.0), control_settings(), NOW
    )
    assert result
    assert adapter.commands == [("select", "select.battery_limit", "80")]


async def test_select_without_numeric_options_is_left_alone() -> None:
    controller, adapter, _ = make_controller(
        limit="select.battery_limit",
        states={"select.battery_limit": "max"},
        options={"select.battery_limit": ["eco", "max"]},
    )
    result = await controller.apply(
        decision(battery_charge_limit_pct=90.0), control_settings(), NOW
    )
    assert not result
    assert adapter.commands == []


async def test_none_fields_leave_everything_untouched() -> None:
    controller, adapter, _ = make_controller(
        states={"switch.battery_charging": "off", "number.battery_limit": "100"}
    )
    result = await controller.apply(decision(), control_settings(), NOW)
    assert not result
    assert adapter.commands == []


async def test_service_failure_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    controller, adapter, _ = make_controller(
        states={"switch.battery_charging": "off"}
    )
    adapter.fail = True
    result = await controller.apply(
        decision(should_enable_battery_charging=True), control_settings(), NOW
    )
    assert not result
    assert "Battery command failed" in caplog.text
    assert controller.last_command_at == NOW  # rate-limits retries


# ---------------------------------------------------------------------------
# Coordinator integration
# ---------------------------------------------------------------------------
BATTERY_CONFIG = {
    CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
    CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
    CONF_BATTERY_ENABLED: True,
    CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
    CONF_BATTERY_POWER_MODE: BatteryPowerMode.NONE.value,
    CONF_BATTERY_CHARGING_ENABLED_ENTITY: "switch.battery_charging",
    CONF_BATTERY_CHARGE_LIMIT_ENTITY: "number.battery_limit",
    CONF_EV_CHARGER_ENABLED: False,
}


async def test_coordinator_controls_battery(hass: HomeAssistant, freezer) -> None:
    set_basic_states(hass)
    set_percent(hass, "sensor.battery_soc", 95)  # above default target? no: 100
    hass.states.async_set("switch.battery_charging", "on")
    hass.states.async_set("number.battery_limit", "80")

    entry = make_entry(
        BATTERY_CONFIG, options={"battery_target_soc": 90.0}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.strategy = Strategy.SOLAR_ONLY
    coordinator.automatic_control = True

    off_calls = async_mock_service(hass, "switch", "turn_off")
    set_calls = async_mock_service(hass, "number", "set_value")

    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # SoC 95 >= target 90: charging is switched off and the limit corrected.
    assert len(off_calls) == 1
    assert off_calls[0].data["entity_id"] == "switch.battery_charging"
    assert len(set_calls) == 1
    assert set_calls[0].data == {"entity_id": "number.battery_limit", "value": 90.0}
    assert coordinator.data.last_command is not None


async def test_no_battery_commands_in_monitor_only(
    hass: HomeAssistant, freezer
) -> None:
    set_basic_states(hass)
    set_percent(hass, "sensor.battery_soc", 95)
    hass.states.async_set("switch.battery_charging", "on")
    hass.states.async_set("number.battery_limit", "80")

    entry = make_entry(BATTERY_CONFIG)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Strategy stays monitor_only and automatic control stays off.

    off_calls = async_mock_service(hass, "switch", "turn_off")
    freezer.tick(timedelta(seconds=65))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not off_calls
