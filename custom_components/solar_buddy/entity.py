"""Base entity: all Solar Buddy entities share one device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolarBuddyCoordinator


class SolarBuddyEntity(CoordinatorEntity[SolarBuddyCoordinator]):
    """Base class providing device info, translation key, and unique id."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarBuddyCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name=coordinator.config_entry.title,
            manufacturer="Solar Buddy",
            model="Energy orchestrator",
        )
