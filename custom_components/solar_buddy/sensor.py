"""Sensors exposing the current evaluation, recommendation and price data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import PriceLevel, Recommendation, SolarBuddyStatus
from .coordinator import SolarBuddyConfigEntry, SolarBuddyData
from .entity import SolarBuddyEntity


@dataclass(frozen=True, kw_only=True)
class SolarBuddySensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[SolarBuddyData], StateType | datetime]
    attributes_fn: Callable[[SolarBuddyData], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[SolarBuddySensorDescription, ...] = (
    SolarBuddySensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in SolarBuddyStatus],
        value_fn=lambda data: data.decision.status.value,
    ),
    SolarBuddySensorDescription(
        key="recommendation",
        translation_key="recommendation",
        device_class=SensorDeviceClass.ENUM,
        options=[reason.value for reason in Recommendation],
        value_fn=lambda data: data.decision.recommendation.value,
        attributes_fn=lambda data: dict(data.decision.reason_placeholders),
    ),
    SolarBuddySensorDescription(
        key="solar_surplus",
        translation_key="solar_surplus",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda data: data.decision.solar_surplus_w,
    ),
    SolarBuddySensorDescription(
        key="available_ev_power",
        translation_key="available_ev_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda data: data.decision.available_ev_power_w,
    ),
    SolarBuddySensorDescription(
        key="recommended_ev_current",
        translation_key="recommended_ev_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        value_fn=lambda data: data.decision.recommended_ev_current_a,
    ),
    SolarBuddySensorDescription(
        key="price_level",
        translation_key="price_level",
        device_class=SensorDeviceClass.ENUM,
        options=[level.value for level in PriceLevel],
        value_fn=lambda data: data.decision.price_level or PriceLevel.UNKNOWN.value,
    ),
    SolarBuddySensorDescription(
        key="next_action",
        translation_key="next_action",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.decision.next_action_at,
    ),
    SolarBuddySensorDescription(
        key="last_evaluation",
        translation_key="last_evaluation",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_evaluation,
    ),
    SolarBuddySensorDescription(
        key="last_command",
        translation_key="last_command",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_command,
    ),
)


# Only created when the user enabled the house battery section.
BATTERY_SENSOR_DESCRIPTIONS: tuple[SolarBuddySensorDescription, ...] = (
    SolarBuddySensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda data: data.snapshot.battery_soc,
    ),
    SolarBuddySensorDescription(
        key="battery_charge_power",
        translation_key="battery_charge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda data: data.snapshot.battery_charge_power_w,
    ),
    SolarBuddySensorDescription(
        key="battery_discharge_power",
        translation_key="battery_discharge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda data: data.snapshot.battery_discharge_power_w,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solar Buddy sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SolarBuddySensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    if coordinator.battery_configured:
        entities.extend(
            SolarBuddySensor(coordinator, description)
            for description in BATTERY_SENSOR_DESCRIPTIONS
        )
    entities.append(SolarBuddyPriceSensor(coordinator))
    async_add_entities(entities)


class SolarBuddySensor(SolarBuddyEntity, SensorEntity):
    """A sensor deriving its value from the latest coordinator data."""

    entity_description: SolarBuddySensorDescription

    def __init__(
        self,
        coordinator,
        description: SolarBuddySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


class SolarBuddyPriceSensor(SolarBuddyEntity, SensorEntity):
    """Current electricity price; unit and currency come from the source."""

    _attr_translation_key = "current_price"
    _attr_suggested_display_precision = 4

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "current_price")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.price_data.current_price

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self.coordinator.data.price_data.unit

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        currency = self.coordinator.data.price_data.currency
        if currency is None:
            return None
        return {"currency": currency}
