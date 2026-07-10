"""Strategy and priority selects.

Both restore the user's last choice across restarts. The strategy defaults
to Monitor only on first installation, so Solar Buddy never controls
anything until the user opts in.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import Priority, Strategy
from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .entity import SolarBuddyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solar Buddy selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [SolarBuddyStrategySelect(coordinator), SolarBuddyPrioritySelect(coordinator)]
    )


class SolarBuddyStrategySelect(SolarBuddyEntity, SelectEntity, RestoreEntity):
    """Choose the operating strategy."""

    def __init__(self, coordinator: SolarBuddyCoordinator) -> None:
        super().__init__(coordinator, "strategy")
        self._attr_options = [strategy.value for strategy in Strategy]

    async def async_added_to_hass(self) -> None:
        """Restore the previously selected strategy."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self.coordinator.strategy = Strategy(last_state.state)

    @property
    def current_option(self) -> str:
        return self.coordinator.strategy.value

    async def async_select_option(self, option: str) -> None:
        """Change strategy and re-evaluate."""
        await self.coordinator.async_set_strategy(Strategy(option))
        self.async_write_ha_state()


class SolarBuddyPrioritySelect(SolarBuddyEntity, SelectEntity, RestoreEntity):
    """Choose how surplus is shared between battery and EV."""

    def __init__(self, coordinator: SolarBuddyCoordinator) -> None:
        super().__init__(coordinator, "priority")
        self._attr_options = [priority.value for priority in Priority]

    async def async_added_to_hass(self) -> None:
        """Restore the previously selected priority."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self.coordinator.priority = Priority(last_state.state)

    @property
    def current_option(self) -> str:
        return self.coordinator.priority.value

    async def async_select_option(self, option: str) -> None:
        """Change priority and re-evaluate."""
        await self.coordinator.async_set_priority(Priority(option))
        self.async_write_ha_state()
