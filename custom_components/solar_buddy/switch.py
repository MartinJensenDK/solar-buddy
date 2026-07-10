"""The automatic-control switch.

Deliberately NOT a RestoreEntity: Solar Buddy must never enable automatic
control by itself after installation, an update, or a Home Assistant restart.
The user re-enables it explicitly.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .entity import SolarBuddyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solar Buddy switch."""
    async_add_entities([SolarBuddyAutomaticControlSwitch(entry.runtime_data)])


class SolarBuddyAutomaticControlSwitch(SolarBuddyEntity, SwitchEntity):
    """Master switch for automatic control; defaults to off."""

    def __init__(self, coordinator: SolarBuddyCoordinator) -> None:
        super().__init__(coordinator, "automatic_control")

    @property
    def is_on(self) -> bool:
        return self.coordinator.automatic_control

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable automatic control."""
        await self.coordinator.async_set_automatic_control(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable automatic control."""
        await self.coordinator.async_set_automatic_control(False)
        self.async_write_ha_state()
