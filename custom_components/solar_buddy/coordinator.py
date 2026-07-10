"""Coordinator: gathers states, normalizes them, and runs the optimizer.

This is not a classic API-polling coordinator; there is no external API. It
combines three triggers into one debounced, lock-protected evaluation:

* state changes of any configured entity (via async_track_state_change_event
  registered in __init__),
* a periodic safety interval (``evaluation_interval``),
* explicit requests (Recalculate button, strategy/priority/control changes).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import EventStateChangedData
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .actuators import ActuatorAdapter
from .const import (
    CONF_BATTERY_CHARGE_POWER_ENTITY,
    CONF_BATTERY_DISCHARGE_POWER_ENTITY,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_MODE,
    CONF_BATTERY_POWER_SIGN,
    CONF_BATTERY_SOC_ENTITY,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_EV_CABLE_CONNECTION_ENTITY,
    CONF_EV_CHARGER_CURRENT_ENTITY,
    CONF_EV_CHARGER_ENABLED,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CONNECTED_STATES,
    CONF_EV_MIN_SOC_ENTITY,
    CONF_EV_SOC_ENTITY,
    CONF_EVALUATION_INTERVAL,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    DEFAULT_EV_CONNECTED_STATES,
    DEFAULT_EVALUATION_INTERVAL,
    DOMAIN,
    BatteryPowerMode,
    BatteryPowerSign,
    Priority,
    Strategy,
)
from .ev_control import EvChargerEntities, EvController
from .models import (
    EnergySnapshot,
    OptimizationDecision,
    OptimizationSettings,
    PriceData,
)
from .normalization import (
    UNKNOWN_STATES,
    non_negative,
    normalize_signed_battery_power,
    parse_float,
    parse_percentage,
    power_to_watts,
)
from .optimizer import evaluate
from .price_parser import parse_price_data

_LOGGER = logging.getLogger(__name__)

type SolarBuddyConfigEntry = ConfigEntry[SolarBuddyCoordinator]

_REFRESH_COOLDOWN = 2.0  # seconds; debounce for bursts of state changes


@dataclass(slots=True)
class SolarBuddyData:
    """Everything the entities need from one evaluation."""

    snapshot: EnergySnapshot
    price_data: PriceData
    decision: OptimizationDecision
    issues: list[str] = field(default_factory=list)
    last_evaluation: datetime | None = None
    last_command: datetime | None = None
    cable_known: bool = False


class SolarBuddyCoordinator(DataUpdateCoordinator[SolarBuddyData]):
    """Holds runtime state and produces SolarBuddyData on every evaluation."""

    config_entry: SolarBuddyConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SolarBuddyConfigEntry) -> None:
        interval = int(
            entry.options.get(CONF_EVALUATION_INTERVAL, DEFAULT_EVALUATION_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=_REFRESH_COOLDOWN, immediate=False
            ),
        )
        # Runtime toggles owned by the select/switch entities. Automatic
        # control is ALWAYS off after (re)start; Solar Buddy never enables
        # itself (see README).
        self.strategy: Strategy = Strategy.MONITOR_ONLY
        self.priority: Priority = Priority.BATTERY_FIRST
        self.automatic_control: bool = False
        self.manual_override_until: datetime | None = None
        self.last_command: datetime | None = None

        self.actuators = ActuatorAdapter(hass, entry.entry_id)
        self._ev_entities = EvChargerEntities.from_entry_data(entry.data)
        self.ev_controller = EvController(
            self.actuators, self._ev_entities, self._read_plain_state
        )
        self._evaluation_lock = asyncio.Lock()
        self._availability: dict[str, bool] = {}

    def _read_plain_state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.state if state is not None else None

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    def _conf(self, key: str) -> str | None:
        value = self.config_entry.data.get(key)
        return value if isinstance(value, str) and value else None

    @property
    def battery_configured(self) -> bool:
        """True when the user enabled the house battery section."""
        return bool(self.config_entry.data.get(CONF_BATTERY_ENABLED))

    @property
    def ev_configured(self) -> bool:
        """True when the user enabled the EV charger section."""
        return bool(self.config_entry.data.get(CONF_EV_CHARGER_ENABLED))

    def tracked_entity_ids(self) -> list[str]:
        """All configured source entities that should trigger re-evaluation."""
        return [
            value
            for key, value in self.config_entry.data.items()
            if key.endswith("_entity") and isinstance(value, str) and value
        ]

    def current_settings(self) -> OptimizationSettings:
        """Snapshot of settings including runtime strategy/priority/control."""
        return OptimizationSettings.from_options(
            self.config_entry.options,
            strategy=self.strategy,
            priority=self.priority,
            automatic_control=self.automatic_control,
            ev_configured=self.ev_configured,
            battery_configured=self.battery_configured,
            manual_override=self.manual_override_active,
        )

    @property
    def manual_override_active(self) -> bool:
        """True while automatic control is paused due to manual changes."""
        until = self.manual_override_until
        return until is not None and dt_util.utcnow() < until

    # ------------------------------------------------------------------
    # Runtime setters used by select/switch/button entities
    # ------------------------------------------------------------------
    async def async_set_strategy(self, strategy: Strategy) -> None:
        """Change the strategy and re-evaluate."""
        if strategy is not self.strategy:
            _LOGGER.info("Strategy changed to %s", strategy)
        self.strategy = strategy
        await self.async_request_refresh()

    async def async_set_priority(self, priority: Priority) -> None:
        """Change the priority and re-evaluate."""
        self.priority = priority
        await self.async_request_refresh()

    async def async_set_automatic_control(self, enabled: bool) -> None:
        """Enable or disable automatic control (user action only)."""
        if enabled != self.automatic_control:
            _LOGGER.info("Automatic control %s", "enabled" if enabled else "disabled")
        self.automatic_control = enabled
        await self.async_request_refresh()

    async def async_clear_manual_override(self) -> None:
        """Clear a manual-override pause and re-evaluate."""
        self.manual_override_until = None
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # State change handling
    # ------------------------------------------------------------------
    @callback
    def handle_source_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Schedule a debounced re-evaluation on source entity changes.

        A user-initiated change (context has a user id) of an entity Solar
        Buddy controls pauses automatic control for the configured time, so
        Solar Buddy never fights the user's manual choice. Its own service
        calls are recognized via their context and ignored here.
        """
        if self.actuators.is_own_context(event.context):
            return
        if (
            self.automatic_control
            and not self.manual_override_active
            and event.context.user_id is not None
            and event.data["entity_id"] in self._ev_entities.controlled_entity_ids()
        ):
            pause_min = self.current_settings().manual_override_pause_min
            if pause_min > 0:
                self.manual_override_until = dt_util.utcnow() + timedelta(
                    minutes=pause_min
                )
                self.ev_controller.reset_pending()
                _LOGGER.info(
                    "Manual change of %s detected; pausing automatic control "
                    "for %s minutes",
                    event.data["entity_id"],
                    pause_min,
                )
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), name=f"{DOMAIN}_refresh"
        )

    # ------------------------------------------------------------------
    # Reading + normalization
    # ------------------------------------------------------------------
    def _log_availability(self, entity_id: str, available: bool) -> None:
        """Log availability transitions once, not on every evaluation."""
        previous = self._availability.get(entity_id)
        if previous is available:
            return
        self._availability[entity_id] = available
        if available:
            if previous is False:
                _LOGGER.info("Entity %s is available again", entity_id)
        else:
            _LOGGER.warning("Entity %s is unavailable or invalid", entity_id)

    def _read_power_w(
        self, key: str, issues: list[str], *, stale_after: timedelta
    ) -> tuple[float | None, bool]:
        """Read a power entity in W. Returns (value, is_stale)."""
        entity_id = self._conf(key)
        if entity_id is None:
            return None, False
        state = self.hass.states.get(entity_id)
        if state is None or state.state.lower() in UNKNOWN_STATES:
            self._log_availability(entity_id, False)
            issues.append(f"unavailable:{entity_id}")
            return None, False
        value = power_to_watts(
            state.state, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        )
        if value is None:
            self._log_availability(entity_id, False)
            issues.append(f"invalid:{entity_id}")
            return None, False
        self._log_availability(entity_id, True)
        stale = dt_util.utcnow() - state.last_updated > stale_after
        if stale:
            issues.append(f"stale:{entity_id}")
        return value, stale

    def _read_percentage(self, key: str, issues: list[str]) -> float | None:
        entity_id = self._conf(key)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state.lower() in UNKNOWN_STATES:
            self._log_availability(entity_id, False)
            issues.append(f"unavailable:{entity_id}")
            return None
        value = parse_percentage(state.state)
        if value is None:
            self._log_availability(entity_id, False)
            issues.append(f"invalid:{entity_id}")
            return None
        self._log_availability(entity_id, True)
        return value

    def _read_battery_flows(
        self, issues: list[str], stale_after: timedelta
    ) -> tuple[float, float]:
        """Return (charge_w, discharge_w), both >= 0."""
        mode = self.config_entry.data.get(CONF_BATTERY_POWER_MODE)
        if mode == BatteryPowerMode.SIGNED:
            value, _ = self._read_power_w(
                CONF_BATTERY_POWER_ENTITY, issues, stale_after=stale_after
            )
            sign = BatteryPowerSign(
                self.config_entry.data.get(
                    CONF_BATTERY_POWER_SIGN, BatteryPowerSign.POSITIVE_IS_CHARGING
                )
            )
            return normalize_signed_battery_power(value, sign)
        if mode == BatteryPowerMode.SEPARATE:
            charge, _ = self._read_power_w(
                CONF_BATTERY_CHARGE_POWER_ENTITY, issues, stale_after=stale_after
            )
            discharge, _ = self._read_power_w(
                CONF_BATTERY_DISCHARGE_POWER_ENTITY, issues, stale_after=stale_after
            )
            return non_negative(charge), non_negative(discharge)
        return 0.0, 0.0

    def _read_ev_connected(self, issues: list[str]) -> bool | None:
        """Interpret the cable connection entity; None when unknown."""
        entity_id = self._conf(CONF_EV_CABLE_CONNECTION_ENTITY)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state.lower() in UNKNOWN_STATES:
            self._log_availability(entity_id, False)
            issues.append(f"unavailable:{entity_id}")
            return None
        self._log_availability(entity_id, True)
        value = state.state.strip().lower()
        if entity_id.startswith("binary_sensor."):
            return value == "on"
        configured = self.config_entry.data.get(
            CONF_EV_CONNECTED_STATES, list(DEFAULT_EV_CONNECTED_STATES)
        )
        connected_states = {str(item).strip().lower() for item in configured}
        return value in connected_states

    def _read_ev_charging(self) -> bool:
        """Best-effort: is the charger currently on?"""
        entity_id = self._conf(CONF_EV_CHARGER_SWITCH_ENTITY)
        if entity_id is None:
            return False
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == "on"

    # ------------------------------------------------------------------
    # Main evaluation
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> SolarBuddyData:
        async with self._evaluation_lock:
            data = self._evaluate()
            await self._async_apply_control(data)
            return data

    async def _async_apply_control(self, data: SolarBuddyData) -> None:
        """Run the EV controller when every safety condition is met.

        Automatic control requires: the switch on, an active strategy, a
        configured EV charger, fresh valid data, known cable status, and no
        active manual override. Otherwise any pending action is dropped.
        """
        settings = self.current_settings()
        allowed = (
            settings.automatic_control
            and settings.strategy is not Strategy.MONITOR_ONLY
            and settings.ev_configured
            and data.decision.data_ready
            and data.cable_known
            and not self.manual_override_active
        )
        if not allowed:
            self.ev_controller.reset_pending()
            return
        try:
            commanded, next_action_at = await self.ev_controller.apply(
                data.decision, settings, dt_util.utcnow()
            )
        except HomeAssistantError as err:
            _LOGGER.warning("EV control failed: %s", err)
            return
        data.decision.next_action_at = next_action_at
        if commanded:
            self.last_command = dt_util.utcnow()
            data.last_command = self.last_command

    def _evaluate(self) -> SolarBuddyData:
        settings = self.current_settings()
        stale_after = timedelta(seconds=settings.data_stale_timeout_s)
        issues: list[str] = []
        now = dt_util.utcnow()

        solar_w, solar_stale = self._read_power_w(
            CONF_SOLAR_PRODUCTION_ENTITY, issues, stale_after=stale_after
        )
        house_w, house_stale = self._read_power_w(
            CONF_HOUSE_CONSUMPTION_ENTITY, issues, stale_after=stale_after
        )
        charge_w, discharge_w = self._read_battery_flows(issues, stale_after)

        ev_connected = self._read_ev_connected(issues)
        price_entity = self._conf(CONF_ELECTRICITY_PRICE_ENTITY)
        price_state = (
            self.hass.states.get(price_entity) if price_entity is not None else None
        )
        if price_state is not None and price_state.state.lower() not in UNKNOWN_STATES:
            price_data = parse_price_data(
                price_state.state,
                dict(price_state.attributes),
                dt_util.get_default_time_zone(),
            )
        else:
            if price_entity is not None:
                issues.append(f"unavailable:{price_entity}")
            price_data = PriceData()

        current_entity = self._conf(CONF_EV_CHARGER_CURRENT_ENTITY)
        ev_current = None
        if current_entity is not None:
            current_state = self.hass.states.get(current_entity)
            if current_state is not None:
                ev_current = parse_float(current_state.state)

        snapshot = EnergySnapshot(
            timestamp=now,
            # Negative production is measurement noise, clamp to 0.
            solar_power_w=non_negative(solar_w),
            house_consumption_w=non_negative(house_w),
            battery_charge_power_w=charge_w,
            battery_discharge_power_w=discharge_w,
            battery_soc=self._read_percentage(CONF_BATTERY_SOC_ENTITY, issues),
            ev_soc=self._read_percentage(CONF_EV_SOC_ENTITY, issues),
            ev_min_soc=self._read_percentage(CONF_EV_MIN_SOC_ENTITY, issues),
            ev_connected=bool(ev_connected),
            ev_charging=self._read_ev_charging(),
            ev_current_a=ev_current,
            current_price=price_data.current_price,
        )

        stale = solar_stale or house_stale
        data_ready = solar_w is not None and house_w is not None and not stale
        decision = evaluate(
            snapshot,
            price_data.intervals,
            settings,
            data_ready=data_ready,
            stale=stale,
        )

        return SolarBuddyData(
            snapshot=snapshot,
            price_data=price_data,
            decision=decision,
            issues=issues,
            last_evaluation=now,
            last_command=self.last_command,
            cable_known=ev_connected is not None,
        )
