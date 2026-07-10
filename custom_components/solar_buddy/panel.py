"""Sidebar panel: the Solar Buddy management page.

Registers a custom frontend panel (a single, build-free ES module) and the
static route that serves it. The panel is a thin view over the integration's
own entities, so it needs no extra state on the Python side.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import SolarBuddyConfigEntry

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "solar-buddy"
PANEL_STATIC_URL = "/solar_buddy_frontend"
PANEL_FILE = "solar-buddy-panel.js"
PANEL_COMPONENT = "solar-buddy-panel"

_STATIC_REGISTERED = f"{DOMAIN}_static_registered"


async def async_register_panel(
    hass: HomeAssistant, entry: SolarBuddyConfigEntry
) -> None:
    """Register the static assets and the sidebar panel for the entry."""
    frontend_dir = Path(__file__).parent / "frontend"

    # The static path is process-global and may only be registered once.
    if not hass.data.get(_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, str(frontend_dir), False)]
        )
        hass.data[_STATIC_REGISTERED] = True

    integration = await async_get_integration(hass, DOMAIN)
    module_url = f"{PANEL_STATIC_URL}/{PANEL_FILE}?v={integration.version}"

    # Drop any stale panel first so a reload never raises "Overwriting panel".
    async_remove_panel(hass)

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        module_url=module_url,
        sidebar_title="Solar Buddy",
        sidebar_icon="mdi:solar-power-variant",
        require_admin=False,
        config={"entry_id": entry.entry_id},
        embed_iframe=False,
    )
    _LOGGER.debug("Registered Solar Buddy panel at /%s", PANEL_URL_PATH)


def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel if it is currently registered."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
