"""Switches: automatic control and per-weekday charging permission.

The automatic-control switch is deliberately NOT restored: Solar Buddy must
never enable automatic control by itself after installation, an update, or a
Home Assistant restart. The weekday switches DO restore the user's schedule.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import WEEKDAYS
from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .entity import SolarBuddyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solar Buddy switches."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [SolarBuddyAutomaticControlSwitch(coordinator)]
    if coordinator.ev_configured:
        entities.extend(
            SolarBuddyChargeDaySwitch(coordinator, day) for day in WEEKDAYS
        )
    async_add_entities(entities)


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


class SolarBuddyChargeDaySwitch(SolarBuddyEntity, SwitchEntity, RestoreEntity):
    """Whether EV charging is allowed on one weekday (default: allowed)."""

    def __init__(self, coordinator: SolarBuddyCoordinator, day: str) -> None:
        super().__init__(coordinator, f"charge_allowed_{day}")
        self._day = day

    async def async_added_to_hass(self) -> None:
        """Restore the previous on/off choice for this day."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            if last_state.state == "on":
                self.coordinator.ev_allowed_days.add(self._day)
            else:
                self.coordinator.ev_allowed_days.discard(self._day)

    @property
    def is_on(self) -> bool:
        return self._day in self.coordinator.ev_allowed_days

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Allow charging on this day."""
        await self.coordinator.async_set_day_allowed(self._day, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Block charging on this day."""
        await self.coordinator.async_set_day_allowed(self._day, False)
        self.async_write_ha_state()
