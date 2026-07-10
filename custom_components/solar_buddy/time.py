"""Time entities: the daily window where EV charging is allowed.

Start == end means the whole day is allowed; a window crossing midnight
(e.g. 22:00-06:00) is supported. Both entities restore the user's last
choice across restarts.
"""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .entity import SolarBuddyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the charging window time entities."""
    coordinator = entry.runtime_data
    if not coordinator.ev_configured:
        return
    async_add_entities(
        [
            SolarBuddyScheduleTime(coordinator, end=False),
            SolarBuddyScheduleTime(coordinator, end=True),
        ]
    )


class SolarBuddyScheduleTime(SolarBuddyEntity, TimeEntity, RestoreEntity):
    """Start or end of the daily EV charging window."""

    def __init__(self, coordinator: SolarBuddyCoordinator, *, end: bool) -> None:
        super().__init__(
            coordinator, "ev_schedule_end" if end else "ev_schedule_start"
        )
        self._end = end

    async def async_added_to_hass(self) -> None:
        """Restore the previously chosen time."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        try:
            restored = time.fromisoformat(last_state.state)
        except ValueError:
            return
        await self.coordinator.async_set_schedule_time(
            end=self._end, value=restored.isoformat()
        )

    @property
    def native_value(self) -> time | None:
        raw = (
            self.coordinator.ev_schedule_end
            if self._end
            else self.coordinator.ev_schedule_start
        )
        try:
            return time.fromisoformat(raw)
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        """Set this end of the charging window."""
        await self.coordinator.async_set_schedule_time(
            end=self._end, value=value.isoformat()
        )
        self.async_write_ha_state()
