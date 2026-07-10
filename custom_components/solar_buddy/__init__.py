"""Solar Buddy: local, deterministic solar-surplus orchestration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .panel import async_register_panel, async_remove_panel

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarBuddyConfigEntry
) -> bool:
    """Set up Solar Buddy from a config entry."""
    coordinator = SolarBuddyCoordinator(hass, entry)
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    # Re-evaluate whenever a configured source entity changes. The listener
    # is removed automatically on unload.
    tracked = coordinator.tracked_entity_ids()
    if tracked:
        entry.async_on_unload(
            async_track_state_change_event(
                hass, tracked, coordinator.handle_source_state_change
            )
        )

    # Reload when options change so new intervals/limits take effect.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The management page is optional UI; a failure here must never prevent
    # the integration itself from running.
    try:
        await async_register_panel(hass, entry)
    except Exception:
        _LOGGER.exception("Failed to register the Solar Buddy management panel")

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: SolarBuddyConfigEntry
) -> None:
    """Reload the entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: SolarBuddyConfigEntry
) -> bool:
    """Unload a config entry; listeners are cleaned up via async_on_unload."""
    async_remove_panel(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, entry: SolarBuddyConfigEntry
) -> bool:
    """Migrate old config entries to the current version."""
    if entry.version > 1:
        # Downgrading from a future major version is not supported.
        return False
    _LOGGER.debug(
        "Config entry at version %s.%s; no migration needed",
        entry.version,
        entry.minor_version,
    )
    return True
