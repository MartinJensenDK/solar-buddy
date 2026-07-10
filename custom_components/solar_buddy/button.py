"""Buttons: force a recalculation and clear a manual-override pause."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SolarBuddyConfigEntry, SolarBuddyCoordinator
from .entity import SolarBuddyEntity


@dataclass(frozen=True, kw_only=True)
class SolarBuddyButtonDescription(ButtonEntityDescription):
    """Button description with an async press handler."""

    press_fn: Callable[[SolarBuddyCoordinator], Awaitable[None]]


BUTTON_DESCRIPTIONS: tuple[SolarBuddyButtonDescription, ...] = (
    SolarBuddyButtonDescription(
        key="recalculate",
        translation_key="recalculate",
        press_fn=lambda coordinator: coordinator.async_request_refresh(),
    ),
    SolarBuddyButtonDescription(
        key="clear_manual_override",
        translation_key="clear_manual_override",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_clear_manual_override(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarBuddyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solar Buddy buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        SolarBuddyButton(coordinator, description)
        for description in BUTTON_DESCRIPTIONS
    )


class SolarBuddyButton(SolarBuddyEntity, ButtonEntity):
    """A button that runs a coordinator action."""

    entity_description: SolarBuddyButtonDescription

    def __init__(
        self,
        coordinator: SolarBuddyCoordinator,
        description: SolarBuddyButtonDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Run the button's action."""
        await self.entity_description.press_fn(self.coordinator)
