"""Actuator layer: the only place Solar Buddy calls Home Assistant services.

The optimizer returns decisions; this module knows how to translate an
``ActuatorCommand`` into the correct service call for the entity's domain,
and how to report what Solar Buddy is allowed to do with a configured entity
(read-only sensors are never written to).

All calls run with a Context created for the config entry so state changes
caused by Solar Buddy itself can be distinguished from manual user actions.
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import ActuatorCapability, CommandAction
from .models import ActuatorCommand

_LOGGER = logging.getLogger(__name__)

_TOGGLE_DOMAINS: Final = frozenset({"switch", "input_boolean"})
_PRESS_DOMAINS: Final = frozenset({"button", "input_button"})
_SCRIPT_DOMAIN: Final = "script"
_NUMBER_DOMAINS: Final = frozenset({"number", "input_number"})
_SELECT_DOMAINS: Final = frozenset({"select"})


def entity_domain(entity_id: str) -> str:
    """Return the domain part of an entity id."""
    return entity_id.partition(".")[0]


def read_write_capability(hass: HomeAssistant, entity_id: str) -> ActuatorCapability:
    """Determine what Solar Buddy may do with an entity.

    Entities in read-only domains (sensor, binary_sensor, ...) are reported
    as READ_ONLY and are never written to.
    """
    if hass.states.get(entity_id) is None:
        return ActuatorCapability.MISSING
    domain = entity_domain(entity_id)
    if domain in _TOGGLE_DOMAINS or domain == _SCRIPT_DOMAIN:
        return ActuatorCapability.TOGGLE
    if domain in _PRESS_DOMAINS:
        return ActuatorCapability.PRESS
    if domain in _NUMBER_DOMAINS:
        return ActuatorCapability.SET_VALUE
    if domain in _SELECT_DOMAINS:
        return ActuatorCapability.SELECT
    return ActuatorCapability.READ_ONLY


class ActuatorAdapter:
    """Executes actuator commands via the correct Home Assistant services."""

    def __init__(self, hass: HomeAssistant, parent_context_id: str) -> None:
        self._hass = hass
        self._parent_context_id = parent_context_id

    def new_context(self) -> Context:
        """Create a context that marks a service call as ours."""
        return Context(parent_id=self._parent_context_id)

    def is_own_context(self, context: Context | None) -> bool:
        """Return True when a state-change context originates from us."""
        return context is not None and context.parent_id == self._parent_context_id

    async def execute(self, command: ActuatorCommand) -> None:
        """Execute one command; raises HomeAssistantError on misuse."""
        match command.action:
            case CommandAction.TURN_ON:
                await self.turn_entity_on(command.entity_id)
            case CommandAction.TURN_OFF:
                await self.turn_entity_off(command.entity_id)
            case CommandAction.PRESS:
                await self.press_entity(command.entity_id)
            case CommandAction.SET_VALUE:
                if not isinstance(command.value, (int, float)):
                    raise HomeAssistantError(
                        f"set_value for {command.entity_id} needs a numeric value"
                    )
                await self.set_numeric_value(command.entity_id, float(command.value))
            case CommandAction.SELECT_OPTION:
                if not isinstance(command.value, str):
                    raise HomeAssistantError(
                        f"select_option for {command.entity_id} needs a string value"
                    )
                await self.set_select_option(command.entity_id, command.value)

    async def turn_entity_on(self, entity_id: str) -> None:
        """Turn on a switch/input_boolean, or run a script."""
        domain = entity_domain(entity_id)
        if domain not in _TOGGLE_DOMAINS and domain != _SCRIPT_DOMAIN:
            raise HomeAssistantError(f"{entity_id} cannot be turned on by Solar Buddy")
        await self._call(domain, "turn_on", entity_id)

    async def turn_entity_off(self, entity_id: str) -> None:
        """Turn off a switch/input_boolean/script."""
        domain = entity_domain(entity_id)
        if domain not in _TOGGLE_DOMAINS and domain != _SCRIPT_DOMAIN:
            raise HomeAssistantError(f"{entity_id} cannot be turned off by Solar Buddy")
        await self._call(domain, "turn_off", entity_id)

    async def press_entity(self, entity_id: str) -> None:
        """Press a button/input_button, or run a script."""
        domain = entity_domain(entity_id)
        if domain in _PRESS_DOMAINS:
            await self._call(domain, "press", entity_id)
        elif domain == _SCRIPT_DOMAIN:
            await self._call(domain, "turn_on", entity_id)
        else:
            raise HomeAssistantError(f"{entity_id} cannot be pressed by Solar Buddy")

    async def set_numeric_value(self, entity_id: str, value: float) -> None:
        """Set a number/input_number value."""
        domain = entity_domain(entity_id)
        if domain not in _NUMBER_DOMAINS:
            raise HomeAssistantError(f"{entity_id} does not accept numeric values")
        await self._call(domain, "set_value", entity_id, {"value": value})

    async def set_select_option(self, entity_id: str, option: str) -> None:
        """Select an option on a select entity."""
        domain = entity_domain(entity_id)
        if domain not in _SELECT_DOMAINS:
            raise HomeAssistantError(f"{entity_id} does not accept options")
        await self._call(domain, "select_option", entity_id, {"option": option})

    async def _call(
        self,
        domain: str,
        service: str,
        entity_id: str,
        extra: dict[str, float | str] | None = None,
    ) -> None:
        data: dict[str, float | str] = {"entity_id": entity_id}
        if extra:
            data.update(extra)
        _LOGGER.debug("Calling %s.%s for %s", domain, service, entity_id)
        await self._hass.services.async_call(
            domain,
            service,
            data,
            blocking=True,
            context=self.new_context(),
        )
