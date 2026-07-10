"""Binary sensors describing data quality and control readiness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import Strategy
from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .entity import SolarBuddyEntity


@dataclass(frozen=True, kw_only=True)
class SolarBuddyBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor."""

    is_on_fn: Callable[[SolarBuddyCoordinator], bool]


BINARY_SENSOR_DESCRIPTIONS: tuple[SolarBuddyBinarySensorDescription, ...] = (
    SolarBuddyBinarySensorDescription(
        key="data_ready",
        translation_key="data_ready",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda coordinator: coordinator.data.decision.data_ready,
    ),
    SolarBuddyBinarySensorDescription(
        key="solar_surplus_available",
        translation_key="solar_surplus_available",
        is_on_fn=lambda coordinator: coordinator.data.decision.solar_surplus_w > 0.0,
    ),
    SolarBuddyBinarySensorDescription(
        key="ev_connected",
        translation_key="ev_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=lambda coordinator: coordinator.data.snapshot.ev_connected,
    ),
    SolarBuddyBinarySensorDescription(
        key="automatic_control_available",
        translation_key="automatic_control_available",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda coordinator: (
            coordinator.data.decision.data_ready
            and coordinator.strategy is not Strategy.MONITOR_ONLY
            and coordinator.ev_configured
            and coordinator.data.cable_known
            and not coordinator.manual_override_active
        ),
    ),
    SolarBuddyBinarySensorDescription(
        key="manual_override",
        translation_key="manual_override",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda coordinator: coordinator.manual_override_active,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solar Buddy binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SolarBuddyBinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class SolarBuddyBinarySensor(SolarBuddyEntity, BinarySensorEntity):
    """A binary sensor deriving its value from the coordinator."""

    entity_description: SolarBuddyBinarySensorDescription

    def __init__(
        self,
        coordinator: SolarBuddyCoordinator,
        description: SolarBuddyBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator)
