"""Grid export control: block export while electricity is too cheap.

The user points Solar Buddy at a switch/input_boolean that allows or blocks
export (on = export allowed) and sets a price threshold in the options. When
the current price is at or below the threshold the switch is turned off, and
back on when the price rises above it. Commands are deduplicated against the
current state, paced by the minimum command interval, and failures are
contained — exactly like the EV and battery controllers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from homeassistant.exceptions import HomeAssistantError

from .ev_control import SupportsEvCommands
from .models import OptimizationDecision, OptimizationSettings

_LOGGER = logging.getLogger(__name__)

_WRITABLE_DOMAINS: Final = ("switch", "input_boolean")


class ExportController:
    """Turns the export switch on/off based on the optimizer decision."""

    def __init__(
        self,
        adapter: SupportsEvCommands,
        switch_entity: str | None,
        read_state: Callable[[str], str | None],
    ) -> None:
        self._adapter = adapter
        self._switch_entity = switch_entity
        self._read_state = read_state
        self.last_command_at: datetime | None = None

    @property
    def writable(self) -> bool:
        """Only switch/input_boolean export entities are controlled."""
        return (
            self._switch_entity is not None
            and self._switch_entity.partition(".")[0] in _WRITABLE_DOMAINS
        )

    def controlled_entity_ids(self) -> set[str]:
        """The export switch (for manual-override detection)."""
        if self.writable and self._switch_entity:
            return {self._switch_entity}
        return set()

    async def apply(
        self,
        decision: OptimizationDecision,
        settings: OptimizationSettings,
        now: datetime,
    ) -> bool:
        """Apply the export decision. Returns True when a command was sent."""
        desired = decision.should_allow_export
        if desired is None or not self.writable or not self._switch_entity:
            return False
        if self.last_command_at is not None:
            next_ok = self.last_command_at + timedelta(
                seconds=settings.minimum_command_interval_s
            )
            if now < next_ok:
                return False
        if self._read_state(self._switch_entity) == ("on" if desired else "off"):
            return False
        try:
            if desired:
                await self._adapter.turn_entity_on(self._switch_entity)
            else:
                await self._adapter.turn_entity_off(self._switch_entity)
        except HomeAssistantError as err:
            self.last_command_at = now
            _LOGGER.warning("Export command failed: %s", err)
            return False
        self.last_command_at = now
        _LOGGER.info("Grid export %s", "allowed" if desired else "blocked")
        return True
