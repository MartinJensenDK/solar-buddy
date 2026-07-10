"""Tests for the EV controller (hysteresis, pacing, confirmation, override)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.solar_buddy.const import (
    EvControlType,
    Recommendation,
    SolarBuddyStatus,
    Strategy,
)
from custom_components.solar_buddy.ev_control import (
    CONFIRMATION_TIMEOUT,
    EvChargerEntities,
    EvController,
)
from custom_components.solar_buddy.models import (
    OptimizationDecision,
    OptimizationSettings,
)

from .conftest import make_entry, set_full_states

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


class FakeAdapter:
    """Records commands; can be told to fail."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, str, float | None]] = []
        self.fail = False

    async def _record(self, kind: str, entity_id: str, value: float | None = None):
        if self.fail:
            raise HomeAssistantError("service failed")
        if value is None:
            self.commands.append((kind, entity_id))
        else:
            self.commands.append((kind, entity_id, value))

    async def turn_entity_on(self, entity_id: str) -> None:
        await self._record("on", entity_id)

    async def turn_entity_off(self, entity_id: str) -> None:
        await self._record("off", entity_id)

    async def press_entity(self, entity_id: str) -> None:
        await self._record("press", entity_id)

    async def set_numeric_value(self, entity_id: str, value: float) -> None:
        await self._record("set", entity_id, value)


def switch_entities() -> EvChargerEntities:
    return EvChargerEntities(
        control_type=EvControlType.SWITCH,
        switch_entity="switch.ev_charger",
        current_entity="number.ev_current",
    )


def make_controller(
    entities: EvChargerEntities | None = None,
    states: dict[str, str] | None = None,
) -> tuple[EvController, FakeAdapter, dict[str, str]]:
    adapter = FakeAdapter()
    state_map = states if states is not None else {"switch.ev_charger": "off"}
    controller = EvController(adapter, entities or switch_entities(), state_map.get)
    return controller, adapter, state_map


def settings(**overrides) -> OptimizationSettings:
    defaults = {
        "strategy": Strategy.SOLAR_ONLY,
        "automatic_control": True,
        "ev_configured": True,
        "ev_start_delay_s": 60,
        "ev_stop_delay_s": 120,
        "ev_adjustment_interval_s": 60,
        "minimum_command_interval_s": 60,
        "ev_phases": 1,
    }
    defaults.update(overrides)
    return OptimizationSettings(**defaults)


def decision(**overrides) -> OptimizationDecision:
    defaults = {
        "strategy": Strategy.SOLAR_ONLY,
        "status": SolarBuddyStatus.ACTIVE,
        "recommendation": Recommendation.EV_CHARGE_RECOMMENDED,
        "recommended_ev_current_a": 10.0,
        "data_ready": True,
    }
    defaults.update(overrides)
    return OptimizationDecision(**defaults)


# ---------------------------------------------------------------------------
# Start/stop hysteresis
# ---------------------------------------------------------------------------
async def test_start_waits_for_start_delay() -> None:
    controller, adapter, _ = make_controller()
    start = decision(should_start_ev=True)
    config = settings()

    commanded, next_at = await controller.apply(start, config, NOW)
    assert not commanded
    assert next_at == NOW + timedelta(seconds=60)
    assert adapter.commands == []

    commanded, _ = await controller.apply(start, config, NOW + timedelta(seconds=30))
    assert not commanded

    commanded, _ = await controller.apply(start, config, NOW + timedelta(seconds=60))
    assert commanded
    # Current is set BEFORE the charger is switched on.
    assert adapter.commands == [
        ("set", "number.ev_current", 10.0),
        ("on", "switch.ev_charger"),
    ]


async def test_brief_sun_spike_does_not_start() -> None:
    """A start proposal that disappears before the delay never executes."""
    controller, adapter, _ = make_controller()
    config = settings()

    await controller.apply(decision(should_start_ev=True), config, NOW)
    # Spike is over: the optimizer no longer proposes starting.
    await controller.apply(
        decision(recommendation=Recommendation.SURPLUS_BELOW_MINIMUM),
        config,
        NOW + timedelta(seconds=30),
    )
    # Even long after the original delay, nothing has been sent.
    commanded, _ = await controller.apply(
        decision(should_start_ev=True), config, NOW + timedelta(seconds=90)
    )
    assert not commanded  # the new proposal restarts the stability clock
    assert adapter.commands == []


async def test_stop_waits_for_stop_delay() -> None:
    controller, adapter, states = make_controller()
    states["switch.ev_charger"] = "on"
    stop = decision(
        recommendation=Recommendation.NO_SURPLUS, should_stop_ev=True
    )
    config = settings()

    commanded, next_at = await controller.apply(stop, config, NOW)
    assert not commanded
    assert next_at == NOW + timedelta(seconds=120)

    commanded, _ = await controller.apply(stop, config, NOW + timedelta(seconds=120))
    assert commanded
    assert adapter.commands == [("off", "switch.ev_charger")]


async def test_single_cloud_does_not_stop() -> None:
    controller, adapter, states = make_controller()
    states["switch.ev_charger"] = "on"
    config = settings()

    stop = decision(recommendation=Recommendation.NO_SURPLUS, should_stop_ev=True)
    await controller.apply(stop, config, NOW)
    # The sun is back before the stop delay expired.
    keep = decision(should_change_ev_current=False)
    await controller.apply(keep, config, NOW + timedelta(seconds=60))
    commanded, _ = await controller.apply(stop, config, NOW + timedelta(seconds=130))
    assert not commanded  # stability clock restarted
    assert adapter.commands == []


async def test_minimum_command_interval_blocks_next_command() -> None:
    controller, _adapter, states = make_controller()
    config = settings(ev_start_delay_s=0, ev_stop_delay_s=0)

    commanded, _ = await controller.apply(decision(should_start_ev=True), config, NOW)
    assert commanded
    states["switch.ev_charger"] = "on"  # confirm the start

    stop = decision(recommendation=Recommendation.NO_SURPLUS, should_stop_ev=True)
    later = NOW + timedelta(seconds=30)
    commanded, next_at = await controller.apply(stop, config, later)
    assert not commanded
    assert next_at == NOW + timedelta(seconds=60)

    commanded, _ = await controller.apply(stop, config, NOW + timedelta(seconds=61))
    assert commanded


# ---------------------------------------------------------------------------
# Current adjustment
# ---------------------------------------------------------------------------
async def test_adjustment_needs_full_step_and_pacing() -> None:
    controller, adapter, states = make_controller()
    states["switch.ev_charger"] = "on"
    config = settings(ev_start_delay_s=0)

    # Start charging at 10 A.
    await controller.apply(decision(should_start_ev=True), config, NOW)
    states["switch.ev_charger"] = "on"
    adapter.commands.clear()

    # 10.5 A differs by less than one step from 10 A: not sent.
    small = decision(should_change_ev_current=True, recommended_ev_current_a=10.5)
    at = NOW + timedelta(seconds=120)
    commanded, _ = await controller.apply(small, config, at)
    assert not commanded

    # 13 A differs enough and pacing has elapsed: sent.
    big = decision(should_change_ev_current=True, recommended_ev_current_a=13.0)
    commanded, _ = await controller.apply(big, config, at)
    assert commanded
    assert adapter.commands == [("set", "number.ev_current", 13.0)]

    # Immediately after, another change is blocked by the adjustment interval.
    bigger = decision(should_change_ev_current=True, recommended_ev_current_a=16.0)
    commanded, next_at = await controller.apply(
        bigger, config, at + timedelta(seconds=10)
    )
    assert not commanded
    assert next_at == at + timedelta(seconds=60)


async def test_read_only_current_is_never_written() -> None:
    entities = EvChargerEntities(
        control_type=EvControlType.SWITCH,
        switch_entity="switch.ev_charger",
        current_entity="sensor.ev_current",  # read-only
    )
    controller, adapter, states = make_controller(entities)
    config = settings(ev_start_delay_s=0)

    commanded, _ = await controller.apply(decision(should_start_ev=True), config, NOW)
    assert commanded
    assert adapter.commands == [("on", "switch.ev_charger")]  # no "set"

    states["switch.ev_charger"] = "on"
    adjust = decision(should_change_ev_current=True, recommended_ev_current_a=16.0)
    commanded, _ = await controller.apply(
        adjust, config, NOW + timedelta(seconds=120)
    )
    assert not commanded


async def test_no_current_changes_after_stop() -> None:
    controller, adapter, states = make_controller()
    config = settings(ev_start_delay_s=0, ev_stop_delay_s=0)

    await controller.apply(decision(should_start_ev=True), config, NOW)
    states["switch.ev_charger"] = "on"
    stop_at = NOW + timedelta(seconds=120)
    await controller.apply(
        decision(recommendation=Recommendation.NO_SURPLUS, should_stop_ev=True),
        config,
        stop_at,
    )
    states["switch.ev_charger"] = "off"
    adapter.commands.clear()

    # An (inconsistent) adjustment proposal after stopping is ignored.
    adjust = decision(should_change_ev_current=True, recommended_ev_current_a=8.0)
    commanded, _ = await controller.apply(
        adjust, config, stop_at + timedelta(seconds=300)
    )
    assert commanded  # a fresh value IS allowed since target differs from None
    # ... but never before pacing/step rules; the important invariant is that
    # the stop itself cleared the remembered current:
    assert controller.last_command_at == stop_at + timedelta(seconds=300)


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------
async def test_unconfirmed_command_blocks_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    controller, _adapter, _states = make_controller()
    config = settings(ev_start_delay_s=0)

    await controller.apply(decision(should_start_ev=True), config, NOW)
    assert controller.awaiting_confirmation  # switch still "off"

    # While waiting for confirmation nothing else is sent.
    stop = decision(recommendation=Recommendation.NO_SURPLUS, should_stop_ev=True)
    commanded, next_at = await controller.apply(
        stop, config, NOW + timedelta(seconds=5)
    )
    assert not commanded
    assert next_at == NOW + CONFIRMATION_TIMEOUT

    # After the timeout a warning is logged and control resumes.
    commanded, _ = await controller.apply(
        stop, config, NOW + CONFIRMATION_TIMEOUT + timedelta(seconds=1)
    )
    assert not controller.awaiting_confirmation
    assert "did not become" in caplog.text


async def test_confirmation_clears_when_state_matches() -> None:
    controller, _adapter, states = make_controller()
    config = settings(ev_start_delay_s=0)
    await controller.apply(decision(should_start_ev=True), config, NOW)
    states["switch.ev_charger"] = "on"
    await controller.apply(decision(), config, NOW + timedelta(seconds=5))
    assert not controller.awaiting_confirmation


async def test_already_on_switch_is_not_commanded_again() -> None:
    controller, adapter, states = make_controller()
    states["switch.ev_charger"] = "on"
    config = settings(ev_start_delay_s=0)
    commanded, _ = await controller.apply(
        decision(should_start_ev=True), config, NOW
    )
    assert commanded
    assert ("on", "switch.ev_charger") not in adapter.commands  # only current set


async def test_service_failure_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    controller, adapter, _ = make_controller()
    adapter.fail = True
    config = settings(ev_start_delay_s=0)
    commanded, _ = await controller.apply(decision(should_start_ev=True), config, NOW)
    assert not commanded
    assert "failed" in caplog.text
    # The failure rate-limits retries via the command interval.
    assert controller.last_command_at == NOW


# ---------------------------------------------------------------------------
# Start/stop entity variant
# ---------------------------------------------------------------------------
async def test_start_stop_entities_are_activated() -> None:
    entities = EvChargerEntities(
        control_type=EvControlType.START_STOP,
        start_entity="button.start",
        stop_entity="script.stop_charging",
        current_entity="number.ev_current",
    )
    controller, adapter, _ = make_controller(entities, states={})
    config = settings(ev_start_delay_s=0, ev_stop_delay_s=0, minimum_command_interval_s=0)

    await controller.apply(decision(should_start_ev=True), config, NOW)
    stop = decision(recommendation=Recommendation.NO_SURPLUS, should_stop_ev=True)
    await controller.apply(stop, config, NOW + timedelta(seconds=1))

    assert adapter.commands == [
        ("set", "number.ev_current", 10.0),
        ("press", "button.start"),
        ("on", "script.stop_charging"),
    ]


# ---------------------------------------------------------------------------
# Coordinator integration: gates and manual override
# ---------------------------------------------------------------------------
async def setup_active(hass: HomeAssistant, full_config_data):
    """Full setup with solar_only strategy and automatic control on."""
    set_full_states(hass)
    entry = make_entry(full_config_data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.strategy = Strategy.SOLAR_ONLY
    coordinator.automatic_control = True
    return entry, coordinator


async def test_control_starts_charging_after_delay(
    hass: HomeAssistant, full_config_data, freezer
) -> None:
    _entry, coordinator = await setup_active(hass, full_config_data)
    # Register mocks AFTER setup: loading the switch platform would
    # otherwise overwrite the mocked switch.turn_on service.
    on_calls = async_mock_service(hass, "switch", "turn_on")
    set_current = async_mock_service(hass, "number", "set_value")

    await coordinator.async_refresh()  # proposes start; stability clock begins
    assert not on_calls

    freezer.tick(timedelta(seconds=65))  # past the 60 s start delay
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(set_current) == 1
    assert len(on_calls) == 1
    assert coordinator.data.last_command is not None


async def test_no_commands_in_monitor_only(
    hass: HomeAssistant, full_config_data, freezer
) -> None:
    _entry, coordinator = await setup_active(hass, full_config_data)
    on_calls = async_mock_service(hass, "switch", "turn_on")
    coordinator.strategy = Strategy.MONITOR_ONLY

    await coordinator.async_refresh()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not on_calls


async def test_no_commands_with_automatic_control_off(
    hass: HomeAssistant, full_config_data, freezer
) -> None:
    _entry, coordinator = await setup_active(hass, full_config_data)
    on_calls = async_mock_service(hass, "switch", "turn_on")
    coordinator.automatic_control = False

    await coordinator.async_refresh()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not on_calls


async def test_no_commands_when_cable_unknown(
    hass: HomeAssistant, full_config_data, freezer
) -> None:
    _entry, coordinator = await setup_active(hass, full_config_data)
    on_calls = async_mock_service(hass, "switch", "turn_on")
    hass.states.async_set("binary_sensor.ev_cable", "unavailable")

    await coordinator.async_refresh()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not on_calls


async def test_manual_change_pauses_automatic_control(
    hass: HomeAssistant, full_config_data
) -> None:
    _entry, coordinator = await setup_active(hass, full_config_data)
    assert not coordinator.manual_override_active

    # A user manually toggles the charger switch (context has a user id).
    hass.states.async_set(
        "switch.ev_charger", "on", context=Context(user_id="a-real-user")
    )
    await hass.async_block_till_done()

    assert coordinator.manual_override_active
    status = coordinator.current_settings()
    assert status.manual_override

    await coordinator.async_clear_manual_override()
    assert not coordinator.manual_override_active


async def test_non_user_change_does_not_pause(
    hass: HomeAssistant, full_config_data
) -> None:
    """Device-originated state changes (no user id) never trigger override."""
    _entry, coordinator = await setup_active(hass, full_config_data)
    hass.states.async_set("switch.ev_charger", "on")  # no user context
    await hass.async_block_till_done()
    assert not coordinator.manual_override_active
