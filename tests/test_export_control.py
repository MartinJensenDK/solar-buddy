"""Tests for the grid export controller."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.exceptions import HomeAssistantError

from custom_components.solar_buddy.const import Recommendation, SolarBuddyStatus, Strategy
from custom_components.solar_buddy.export_control import ExportController
from custom_components.solar_buddy.models import (
    OptimizationDecision,
    OptimizationSettings,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


class FakeAdapter:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []
        self.fail = False

    async def _record(self, kind: str, entity_id: str) -> None:
        if self.fail:
            raise HomeAssistantError("service failed")
        self.commands.append((kind, entity_id))

    async def turn_entity_on(self, entity_id: str) -> None:
        await self._record("on", entity_id)

    async def turn_entity_off(self, entity_id: str) -> None:
        await self._record("off", entity_id)

    async def press_entity(self, entity_id: str) -> None:
        await self._record("press", entity_id)

    async def set_numeric_value(self, entity_id: str, value: float) -> None:
        await self._record("set", entity_id)


def make(entity: str | None = "switch.grid_export", state: str = "on"):
    adapter = FakeAdapter()
    states = {entity: state} if entity else {}
    controller = ExportController(adapter, entity, states.get)
    return controller, adapter, states


def decision(allow: bool | None) -> OptimizationDecision:
    return OptimizationDecision(
        strategy=Strategy.SOLAR_ONLY,
        status=SolarBuddyStatus.ACTIVE,
        recommendation=Recommendation.NO_EV_CONFIGURED,
        should_allow_export=allow,
        data_ready=True,
    )


SETTINGS = OptimizationSettings(minimum_command_interval_s=60)


async def test_blocks_and_allows_export() -> None:
    controller, adapter, states = make(state="on")
    assert await controller.apply(decision(False), SETTINGS, NOW)
    states["switch.grid_export"] = "off"
    assert await controller.apply(
        decision(True), SETTINGS, NOW + timedelta(seconds=61)
    )
    assert adapter.commands == [
        ("off", "switch.grid_export"),
        ("on", "switch.grid_export"),
    ]


async def test_dedupe_against_current_state() -> None:
    controller, adapter, _ = make(state="on")
    assert not await controller.apply(decision(True), SETTINGS, NOW)
    assert adapter.commands == []


async def test_none_leaves_untouched() -> None:
    controller, adapter, _ = make(state="on")
    assert not await controller.apply(decision(None), SETTINGS, NOW)
    assert adapter.commands == []


async def test_pacing() -> None:
    controller, adapter, states = make(state="on")
    assert await controller.apply(decision(False), SETTINGS, NOW)
    states["switch.grid_export"] = "off"
    assert not await controller.apply(
        decision(True), SETTINGS, NOW + timedelta(seconds=30)
    )
    assert len(adapter.commands) == 1


async def test_read_only_entity_is_never_written() -> None:
    controller, adapter, _ = make(entity="binary_sensor.exporting", state="on")
    assert not controller.writable
    assert controller.controlled_entity_ids() == set()
    assert not await controller.apply(decision(False), SETTINGS, NOW)
    assert adapter.commands == []


async def test_failure_is_contained(caplog) -> None:
    controller, adapter, _ = make(state="on")
    adapter.fail = True
    assert not await controller.apply(decision(False), SETTINGS, NOW)
    assert "Export command failed" in caplog.text
    assert controller.last_command_at == NOW
