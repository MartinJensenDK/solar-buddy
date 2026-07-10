"""Diagnostics for Solar Buddy config entries.

Entity ids and normalized values are included by design; the integration
holds no tokens, passwords or other secrets.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .actuators import read_write_capability
from .const import (
    CONF_BATTERY_CHARGE_LIMIT_ENTITY,
    CONF_BATTERY_CHARGING_ENABLED_ENTITY,
    CONF_EV_CHARGER_CURRENT_ENTITY,
    CONF_EV_CHARGER_START_ENTITY,
    CONF_EV_CHARGER_STOP_ENTITY,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    DOMAIN,
)
from .coordinator import SolarBuddyConfigEntry

_ACTUATOR_KEYS = (
    CONF_BATTERY_CHARGING_ENABLED_ENTITY,
    CONF_BATTERY_CHARGE_LIMIT_ENTITY,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CHARGER_START_ENTITY,
    CONF_EV_CHARGER_STOP_ENTITY,
    CONF_EV_CHARGER_CURRENT_ENTITY,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarBuddyConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    integration = await async_get_integration(hass, DOMAIN)
    data = coordinator.data

    capabilities = {
        key: read_write_capability(hass, entity_id).value
        for key in _ACTUATOR_KEYS
        if (entity_id := entry.data.get(key))
    }

    return {
        "version": integration.version,
        "strategy": coordinator.strategy.value,
        "priority": coordinator.priority.value,
        "automatic_control": coordinator.automatic_control,
        "manual_override_active": coordinator.manual_override_active,
        "configured_entities": dict(entry.data),
        "options": dict(entry.options),
        "actuator_capabilities": capabilities,
        "snapshot": asdict(data.snapshot) if data else None,
        "decision": asdict(data.decision) if data else None,
        "price_interval_count": len(data.price_data.intervals) if data else 0,
        "data_quality_issues": list(data.issues) if data else [],
        "last_evaluation": data.last_evaluation if data else None,
        "last_command": data.last_command if data else None,
    }
