"""Lifecycle tests: setup, unload, reload, migration, diagnostics."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.solar_buddy import async_migrate_entry
from custom_components.solar_buddy.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import make_entry, set_basic_states, set_full_states, set_power


async def setup_entry(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, basic_config_data) -> None:
    set_basic_states(hass)
    entry = make_entry(basic_config_data)
    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.data is not None
    assert coordinator.data.decision.solar_surplus_w == 2200.0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_with_unavailable_sensors(
    hass: HomeAssistant, basic_config_data
) -> None:
    """Missing source entities must not break setup; data is just not ready."""
    entry = make_entry(basic_config_data)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.data.decision.data_ready is False


async def test_reload(hass: HomeAssistant, full_config_data) -> None:
    set_full_states(hass)
    entry = make_entry(full_config_data)
    await setup_entry(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.data is not None


async def test_state_change_triggers_reevaluation(
    hass: HomeAssistant, basic_config_data, freezer
) -> None:
    set_basic_states(hass)
    entry = make_entry(basic_config_data)
    await setup_entry(hass, entry)
    assert entry.runtime_data.data.decision.solar_surplus_w == 2200.0

    set_power(hass, "sensor.solar_production", 6000)
    await hass.async_block_till_done()
    # The refresh is debounced; jump past the cooldown.
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert entry.runtime_data.data.decision.solar_surplus_w == 5200.0


async def test_periodic_safety_evaluation(
    hass: HomeAssistant, basic_config_data, freezer
) -> None:
    set_basic_states(hass)
    entry = make_entry(basic_config_data)
    await setup_entry(hass, entry)
    first = entry.runtime_data.data.last_evaluation

    freezer.tick(timedelta(seconds=45))  # default evaluation_interval is 30 s
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert entry.runtime_data.data.last_evaluation != first


async def test_migration_current_version(hass: HomeAssistant, basic_config_data) -> None:
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is True


async def test_diagnostics(hass: HomeAssistant, full_config_data) -> None:
    set_full_states(hass)
    entry = make_entry(full_config_data)
    await setup_entry(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    manifest = json.loads(
        Path("custom_components/solar_buddy/manifest.json").read_text()
    )
    assert diagnostics["version"] == manifest["version"]
    assert diagnostics["strategy"] == "monitor_only"
    assert diagnostics["automatic_control"] is False
    assert diagnostics["snapshot"]["solar_power_w"] == 3000.0
    assert diagnostics["actuator_capabilities"]["ev_charger_switch_entity"] == "toggle"
    assert diagnostics["actuator_capabilities"]["ev_charger_current_entity"] == "set_value"
    # No secrets: the integration never holds tokens or passwords.
    assert "token" not in str(diagnostics).lower()
