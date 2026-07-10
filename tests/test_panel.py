"""The management panel registers and removes a sidebar entry."""

from __future__ import annotations

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.solar_buddy.panel import PANEL_COMPONENT, PANEL_URL_PATH

from .conftest import make_entry, set_basic_states


async def test_panel_registered_and_removed(
    hass: HomeAssistant, basic_config_data
) -> None:
    # The real frontend package isn't present in the test env; http is all the
    # panel registration actually needs (static path + the panels registry).
    assert await async_setup_component(hass, "http", {})
    set_basic_states(hass)
    entry = make_entry(basic_config_data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    panels = hass.data[frontend.DATA_PANELS]
    assert PANEL_URL_PATH in panels
    assert panels[PANEL_URL_PATH].config["_panel_custom"]["name"] == PANEL_COMPONENT
    assert panels[PANEL_URL_PATH].config["entry_id"] == entry.entry_id

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert PANEL_URL_PATH not in hass.data[frontend.DATA_PANELS]
