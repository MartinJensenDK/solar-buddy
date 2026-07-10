"""EV charging control: hysteresis, command pacing, and confirmation.

The controller receives optimizer decisions and decides IF and WHEN to act:

* a proposed start must stay stable for ``ev_start_delay`` (no starts on a
  brief sun spike),
* a proposed stop must stay stable for ``ev_stop_delay`` (no stops on a
  single cloud),
* current adjustments are paced by ``ev_adjustment_interval`` and only sent
  when the target differs from the last sent value by at least one step,
* every command respects ``minimum_command_interval``,
* after a start/stop command the expected state change is verified; while a
  confirmation is outstanding no new commands are sent, and a missing
  confirmation is logged as a warning.

Start sequence: set the charging current first (when writable), then start.
Stop sequence: stop only; no current changes until charging starts again.

Dependencies (actuator adapter + state reader) are injected so the whole
controller is unit-testable without a Home Assistant runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_EV_CHARGER_CURRENT_ENTITY,
    CONF_EV_CHARGER_START_ENTITY,
    CONF_EV_CHARGER_STOP_ENTITY,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CONTROL_TYPE,
    EvControlType,
)
from .models import OptimizationDecision, OptimizationSettings

_LOGGER = logging.getLogger(__name__)

CONFIRMATION_TIMEOUT = timedelta(seconds=30)

_WRITABLE_CURRENT_DOMAINS = ("number", "input_number")
_PRESS_DOMAINS = ("button", "input_button")


class EvAction(StrEnum):
    """Actions the controller can be waiting to perform."""

    START = "start"
    STOP = "stop"


class SupportsEvCommands(Protocol):
    """The subset of the actuator adapter the controller needs."""

    async def turn_entity_on(self, entity_id: str) -> None: ...

    async def turn_entity_off(self, entity_id: str) -> None: ...

    async def press_entity(self, entity_id: str) -> None: ...

    async def set_numeric_value(self, entity_id: str, value: float) -> None: ...


@dataclass(slots=True)
class EvChargerEntities:
    """The control entities configured for the EV charger."""

    control_type: EvControlType
    switch_entity: str | None = None
    start_entity: str | None = None
    stop_entity: str | None = None
    current_entity: str | None = None

    @classmethod
    def from_entry_data(cls, data: Mapping[str, Any]) -> EvChargerEntities:
        """Build from a config entry's data mapping."""
        return cls(
            control_type=EvControlType(
                data.get(CONF_EV_CONTROL_TYPE, EvControlType.SWITCH.value)
            ),
            switch_entity=data.get(CONF_EV_CHARGER_SWITCH_ENTITY),
            start_entity=data.get(CONF_EV_CHARGER_START_ENTITY),
            stop_entity=data.get(CONF_EV_CHARGER_STOP_ENTITY),
            current_entity=data.get(CONF_EV_CHARGER_CURRENT_ENTITY),
        )

    @property
    def current_writable(self) -> bool:
        """Only number/input_number currents are regulated automatically."""
        return self.current_entity is not None and self.current_entity.startswith(
            tuple(f"{domain}." for domain in _WRITABLE_CURRENT_DOMAINS)
        )

    def controlled_entity_ids(self) -> set[str]:
        """Entities Solar Buddy writes to (for manual-override detection)."""
        return {
            entity_id
            for entity_id in (
                self.switch_entity,
                self.start_entity,
                self.stop_entity,
                self.current_entity,
            )
            if entity_id
        }


@dataclass(slots=True)
class _PendingConfirmation:
    entity_id: str
    expected_state: str
    deadline: datetime


class EvController:
    """Turns stable optimizer decisions into actuator commands."""

    def __init__(
        self,
        adapter: SupportsEvCommands,
        entities: EvChargerEntities,
        read_state: Callable[[str], str | None],
    ) -> None:
        self._adapter = adapter
        self._entities = entities
        self._read_state = read_state
        self._pending_action: EvAction | None = None
        self._pending_since: datetime | None = None
        self._confirmation: _PendingConfirmation | None = None
        self.last_command_at: datetime | None = None
        self._last_current_sent: float | None = None
        self._last_adjustment_at: datetime | None = None

    @property
    def awaiting_confirmation(self) -> bool:
        """True while a previous command has not yet been confirmed."""
        return self._confirmation is not None

    def reset_pending(self) -> None:
        """Forget a pending start/stop (control not currently allowed)."""
        self._pending_action = None
        self._pending_since = None

    async def apply(
        self,
        decision: OptimizationDecision,
        settings: OptimizationSettings,
        now: datetime,
    ) -> tuple[bool, datetime | None]:
        """Act on a decision if it has been stable long enough.

        Returns (command_sent, next_action_at). ``next_action_at`` is when
        the currently blocked action becomes possible, or None.
        """
        self._check_confirmation(now)
        if self._confirmation is not None:
            return False, self._confirmation.deadline

        if decision.should_start_ev:
            desired: EvAction | None = EvAction.START
        elif decision.should_stop_ev:
            desired = EvAction.STOP
        else:
            desired = None

        if desired is None:
            self.reset_pending()
            if decision.should_change_ev_current:
                return await self._maybe_adjust(decision, settings, now)
            return False, None

        if desired is not self._pending_action:
            self._pending_action = desired
            self._pending_since = now

        delay_s = (
            settings.ev_start_delay_s
            if desired is EvAction.START
            else settings.ev_stop_delay_s
        )
        assert self._pending_since is not None
        ready_at = self._pending_since + timedelta(seconds=delay_s)
        if now < ready_at:
            return False, ready_at

        if (blocked_until := self._command_interval_block(settings, now)) is not None:
            return False, blocked_until

        try:
            if desired is EvAction.START:
                await self._start(decision, settings, now)
            else:
                await self._stop(now)
        except HomeAssistantError as err:
            # Rate-limit retries of a failing service via last_command_at.
            self.last_command_at = now
            _LOGGER.warning("EV %s command failed: %s", desired.value, err)
            return False, None
        self.reset_pending()
        return True, None

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------
    async def _start(
        self,
        decision: OptimizationDecision,
        settings: OptimizationSettings,
        now: datetime,
    ) -> None:
        current = min(
            max(decision.recommended_ev_current_a, settings.ev_min_current),
            settings.ev_max_current,
        )
        # 1) set the correct current first, when the entity is writable
        if self._entities.current_writable and self._entities.current_entity:
            await self._adapter.set_numeric_value(self._entities.current_entity, current)
            self._last_current_sent = current
            self._last_adjustment_at = now
        # 2) then start charging
        if (
            self._entities.control_type is EvControlType.SWITCH
            and self._entities.switch_entity
        ):
            if self._read_state(self._entities.switch_entity) != "on":
                await self._adapter.turn_entity_on(self._entities.switch_entity)
                self._expect(self._entities.switch_entity, "on", now)
        elif self._entities.start_entity:
            await self._activate(self._entities.start_entity)
        self.last_command_at = now
        _LOGGER.info("EV charging started at %.1f A", current)

    async def _stop(self, now: datetime) -> None:
        if (
            self._entities.control_type is EvControlType.SWITCH
            and self._entities.switch_entity
        ):
            if self._read_state(self._entities.switch_entity) != "off":
                await self._adapter.turn_entity_off(self._entities.switch_entity)
                self._expect(self._entities.switch_entity, "off", now)
        elif self._entities.stop_entity:
            await self._activate(self._entities.stop_entity)
        self.last_command_at = now
        # No current changes until charging is started again.
        self._last_current_sent = None
        _LOGGER.info("EV charging stopped")

    async def _maybe_adjust(
        self,
        decision: OptimizationDecision,
        settings: OptimizationSettings,
        now: datetime,
    ) -> tuple[bool, datetime | None]:
        """Send a new charging current if paced and meaningfully different."""
        if not self._entities.current_writable or not self._entities.current_entity:
            return False, None
        target = decision.recommended_ev_current_a
        if target <= 0.0:
            return False, None
        if (
            self._last_current_sent is not None
            and abs(target - self._last_current_sent) < settings.ev_current_step
        ):
            return False, None
        if self._last_adjustment_at is not None:
            next_ok = self._last_adjustment_at + timedelta(
                seconds=settings.ev_adjustment_interval_s
            )
            if now < next_ok:
                return False, next_ok
        if (blocked_until := self._command_interval_block(settings, now)) is not None:
            return False, blocked_until

        try:
            await self._adapter.set_numeric_value(self._entities.current_entity, target)
        except HomeAssistantError as err:
            self.last_command_at = now
            _LOGGER.warning("EV current adjustment failed: %s", err)
            return False, None
        self._last_current_sent = target
        self._last_adjustment_at = now
        self.last_command_at = now
        _LOGGER.info("EV charging current adjusted to %.1f A", target)
        return True, None

    async def _activate(self, entity_id: str) -> None:
        """Activate a start/stop entity according to its domain."""
        domain = entity_id.partition(".")[0]
        if domain in _PRESS_DOMAINS:
            await self._adapter.press_entity(entity_id)
        else:  # script, switch, input_boolean
            await self._adapter.turn_entity_on(entity_id)

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------
    def _expect(self, entity_id: str, state: str, now: datetime) -> None:
        self._confirmation = _PendingConfirmation(
            entity_id=entity_id,
            expected_state=state,
            deadline=now + CONFIRMATION_TIMEOUT,
        )

    def _check_confirmation(self, now: datetime) -> None:
        confirmation = self._confirmation
        if confirmation is None:
            return
        if self._read_state(confirmation.entity_id) == confirmation.expected_state:
            self._confirmation = None
            return
        if now >= confirmation.deadline:
            _LOGGER.warning(
                "%s did not become '%s' after Solar Buddy's command",
                confirmation.entity_id,
                confirmation.expected_state,
            )
            self._confirmation = None

    def _command_interval_block(
        self, settings: OptimizationSettings, now: datetime
    ) -> datetime | None:
        """Return when the minimum command interval expires, if still active."""
        if self.last_command_at is None:
            return None
        next_ok = self.last_command_at + timedelta(
            seconds=settings.minimum_command_interval_s
        )
        return next_ok if now < next_ok else None
