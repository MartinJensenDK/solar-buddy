"""Number entity: the price at or below which grid export is blocked.

Only created when a grid export switch is configured. The value is in the
price sensor's own unit (no hardcoded currency) and restores across
restarts. Default 0.0 blocks export at zero and negative prices.
"""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .entity import SolarBuddyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the export threshold number."""
    coordinator = entry.runtime_data
    if not coordinator.export_configured:
        return
    async_add_entities([SolarBuddyExportThresholdNumber(coordinator)])


class SolarBuddyExportThresholdNumber(SolarBuddyEntity, RestoreNumber):
    """Block export while the current electricity price is at or below this."""

    _attr_native_min_value = -100.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SolarBuddyCoordinator) -> None:
        super().__init__(coordinator, "export_price_threshold")

    async def async_added_to_hass(self) -> None:
        """Restore the previously chosen threshold."""
        await super().async_added_to_hass()
        data = await self.async_get_last_number_data()
        if data is not None and data.native_value is not None:
            await self.coordinator.async_set_export_threshold(data.native_value)

    @property
    def native_value(self) -> float:
        return self.coordinator.export_price_threshold

    async def async_set_native_value(self, value: float) -> None:
        """Set the export price threshold."""
        await self.coordinator.async_set_export_threshold(value)
        self.async_write_ha_state()
