"""House battery control: charging on/off and charge limit.

The battery entities may be read-only (sensor/binary_sensor for status,
sensor for the limit) or writable (switch/input_boolean, respectively
number/input_number/select). Solar Buddy inspects the entity domain and only
ever writes to writable domains; read-only entities are used for monitoring.

Commands are deduplicated against the current state and paced by the
``minimum_command_interval``, with the same failure containment as the EV
controller. Dependencies are injected so the controller is unit-testable
without a Home Assistant runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_BATTERY_CHARGE_LIMIT_ENTITY,
    CONF_BATTERY_CHARGING_ENABLED_ENTITY,
)
from .ev_control import SupportsEvCommands
from .models import OptimizationDecision, OptimizationSettings
from .normalization import parse_float

_LOGGER = logging.getLogger(__name__)

_WRITABLE_TOGGLE_DOMAINS: Final = ("switch", "input_boolean")
_WRITABLE_NUMBER_DOMAINS: Final = ("number", "input_number")
_SELECT_DOMAIN: Final = "select"

# A limit difference below this is not worth a service call.
_LIMIT_TOLERANCE_PCT: Final = 0.5


class SupportsBatteryCommands(SupportsEvCommands):
    """The battery controller also selects options."""

    async def set_select_option(self, entity_id: str, option: str) -> None: ...


def _domain(entity_id: str) -> str:
    return entity_id.partition(".")[0]


class BatteryController:
    """Executes battery decisions via the actuator adapter."""

    def __init__(
        self,
        adapter: SupportsBatteryCommands,
        charging_entity: str | None,
        limit_entity: str | None,
        read_state: Callable[[str], str | None],
        read_options: Callable[[str], list[str] | None],
    ) -> None:
        self._adapter = adapter
        self._charging_entity = charging_entity
        self._limit_entity = limit_entity
        self._read_state = read_state
        self._read_options = read_options
        self.last_command_at: datetime | None = None

    @classmethod
    def from_entry_data(
        cls,
        adapter: SupportsBatteryCommands,
        data: Mapping[str, Any],
        read_state: Callable[[str], str | None],
        read_options: Callable[[str], list[str] | None],
    ) -> BatteryController:
        """Build from a config entry's data mapping."""
        return cls(
            adapter,
            data.get(CONF_BATTERY_CHARGING_ENABLED_ENTITY),
            data.get(CONF_BATTERY_CHARGE_LIMIT_ENTITY),
            read_state,
            read_options,
        )

    @property
    def charging_writable(self) -> bool:
        """Only switch/input_boolean charging entities are controlled."""
        return (
            self._charging_entity is not None
            and _domain(self._charging_entity) in _WRITABLE_TOGGLE_DOMAINS
        )

    @property
    def limit_writable(self) -> bool:
        """Only number/input_number/select limit entities are controlled."""
        if self._limit_entity is None:
            return False
        domain = _domain(self._limit_entity)
        return domain in _WRITABLE_NUMBER_DOMAINS or domain == _SELECT_DOMAIN

    def controlled_entity_ids(self) -> set[str]:
        """Writable battery entities (for manual-override detection)."""
        controlled: set[str] = set()
        if self.charging_writable and self._charging_entity:
            controlled.add(self._charging_entity)
        if self.limit_writable and self._limit_entity:
            controlled.add(self._limit_entity)
        return controlled

    async def apply(
        self,
        decision: OptimizationDecision,
        settings: OptimizationSettings,
        now: datetime,
    ) -> bool:
        """Apply the battery parts of a decision. Returns True on a command."""
        if self.last_command_at is not None:
            next_ok = self.last_command_at + timedelta(
                seconds=settings.minimum_command_interval_s
            )
            if now < next_ok:
                return False

        commanded = False
        try:
            commanded |= await self._apply_charging_toggle(decision)
            commanded |= await self._apply_limit(decision)
        except HomeAssistantError as err:
            self.last_command_at = now
            _LOGGER.warning("Battery command failed: %s", err)
            return False
        if commanded:
            self.last_command_at = now
        return commanded

    async def _apply_charging_toggle(self, decision: OptimizationDecision) -> bool:
        desired = decision.should_enable_battery_charging
        if desired is None or not self.charging_writable or not self._charging_entity:
            return False
        current = self._read_state(self._charging_entity)
        if current == ("on" if desired else "off"):
            return False
        if desired:
            await self._adapter.turn_entity_on(self._charging_entity)
        else:
            await self._adapter.turn_entity_off(self._charging_entity)
        _LOGGER.info(
            "Battery charging %s", "enabled" if desired else "disabled"
        )
        return True

    async def _apply_limit(self, decision: OptimizationDecision) -> bool:
        desired = decision.battery_charge_limit_pct
        if desired is None or not self.limit_writable or not self._limit_entity:
            return False
        if _domain(self._limit_entity) == _SELECT_DOMAIN:
            return await self._apply_limit_select(desired)
        current = parse_float(self._read_state(self._limit_entity))
        if current is not None and abs(current - desired) < _LIMIT_TOLERANCE_PCT:
            return False
        await self._adapter.set_numeric_value(self._limit_entity, desired)
        _LOGGER.info("Battery charge limit set to %.0f%%", desired)
        return True

    async def _apply_limit_select(self, desired: float) -> bool:
        """Pick the numeric select option closest to the desired limit."""
        assert self._limit_entity is not None
        options = self._read_options(self._limit_entity) or []
        numeric = [
            (option, value)
            for option in options
            if (value := parse_float(option)) is not None
        ]
        if not numeric:
            _LOGGER.debug(
                "%s has no numeric options; leaving it alone", self._limit_entity
            )
            return False
        best_option, best_value = min(
            numeric, key=lambda pair: (abs(pair[1] - desired), pair[1])
        )
        current = self._read_state(self._limit_entity)
        if current == best_option:
            return False
        await self._adapter.set_select_option(self._limit_entity, best_option)
        _LOGGER.info(
            "Battery charge limit option set to %s (target %.0f%%)",
            best_option,
            best_value,
        )
        return True
