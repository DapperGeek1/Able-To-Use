"""Polling, device discovery, and account-scoped command dispatch."""

from asyncio import Event, Lock, Semaphore, gather, wait_for
from dataclasses import dataclass, field, replace
from datetime import timedelta
import logging
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AbleAuthError, AbleClient, AbleError
from .const import (
    CONF_PARTIAL_POSITIONS, CONF_SCAN_INTERVAL, DEFAULT_PARTIAL_POSITION,
    DEFAULT_SCAN_INTERVAL, DOMAIN,
)
from .models import Action, Device

_LOGGER = logging.getLogger(__name__)
CATALOG_REFRESH = 300
ACTIVE_POLL_INTERVAL = timedelta(seconds=1)
ACTION_START_TIMEOUT = 12
ACTION_MAX_DURATION = 90
ACTION_SETTLE_TIME = 3
MOVING_STATES = frozenset({"opening", "closing", "moving"})


@dataclass(slots=True)
class ActionPoll:
    """Follow reported motion without treating a command acknowledgment as completion."""

    before: Device
    role: str | None
    started: float
    position: int | None
    relay: int | None
    _last: Device = field(init=False)
    _changed_at: float | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._last = self.before

    def complete(self, device: Device, now: float) -> bool:
        """Return whether the report or a timeout ends this action's fast polling."""
        if now - self.started >= ACTION_MAX_DURATION:
            return True

        if self.relay is not None:
            expected_on = self.role == "on"
            return device.relays[self.relay - 1] is expected_on or now - self.started >= ACTION_START_TIMEOUT

        moving = device.state in MOVING_STATES
        if moving or (device.state, device.position, device.relays) != (self._last.state, self._last.position, self._last.relays):
            self._changed_at = now
        self._last = device
        if moving:
            return False

        stopping = self.role == "stop" or (self.role == "start" and self.before.state in MOVING_STATES)
        if stopping and device.state is not None:
            return True

        target = {"open": 100, "close": 0, "target": self.position, "favorite": self.before.favorite}.get(self.role)
        if target is not None:
            if device.position == target:
                return True
            if device.position is None and (target, device.state) in {(100, "open"), (0, "closed")}:
                return True
        if self._changed_at is None:
            return now - self.started >= ACTION_START_TIMEOUT
        return target is None and now - self._changed_at >= ACTION_SETTLE_TIME


@dataclass(slots=True)
class GateMove:
    """Track a positioning request through cloud updates and manual cancellation."""

    updated: Event = field(default_factory=Event)
    command: Lock = field(default_factory=Lock)
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True
        self.updated.set()


def device_info(device: Device) -> dr.DeviceInfo:
    """Identify a physical controller by its cloud UUID."""
    return dr.DeviceInfo(
        identifiers={(DOMAIN, device.id)}, manufacturer="Able", name=device.name,
        model=device.subtype or device.kind, serial_number=device.serial, sw_version=device.firmware,
    )


class AbleCoordinator(DataUpdateCoordinator[dict[str, Device]]):
    """Share one status poll among all entities in an account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AbleClient) -> None:
        self._normal_interval = timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, always_update=False,
            update_interval=self._normal_interval,
        )
        self.entry = entry
        self.client = client
        self._catalog: dict[str, tuple[float, tuple[Action, ...]]] = {}
        self._catalog_slots = Semaphore(4)
        self._commands: dict[tuple[str, bool], Event] = {}
        self._latest_command: dict[str, Event] = {}
        self._action_polls: dict[str, ActionPoll] = {}
        self._gate_moves: dict[str, GateMove] = {}
        self._read_started = self._last_read = 0.0

    def _set_poll_interval(self) -> None:
        self.update_interval = ACTIVE_POLL_INTERVAL if self._action_polls or self._gate_moves else self._normal_interval

    def _finish_action_polls(self, devices: dict[str, Device], read_started: float) -> None:
        now = monotonic()
        for device_id, action in list(self._action_polls.items()):
            if read_started < action.started:
                continue
            if (device := devices.get(device_id)) is None or action.complete(device, now):
                del self._action_polls[device_id]
        self._set_poll_interval()

    async def _with_catalog(self, device: Device) -> Device:
        expires, actions = self._catalog.get(device.id, (0.0, ()))
        if monotonic() < expires:
            return replace(device, actions=actions)
        try:
            async with self._catalog_slots:
                device = await self.client.async_device_actions(device)
        except AbleAuthError:
            raise
        except AbleError:
            _LOGGER.debug("Could not refresh an Able action catalog")
            self._catalog[device.id] = (monotonic() + 30, ())
            return device
        self._catalog[device.id] = (monotonic() + CATALOG_REFRESH, device.actions)
        return device

    async def _async_update_data(self) -> dict[str, Device]:
        self._read_started = monotonic()
        try:
            devices = await self.client.async_devices()
            self._finish_action_polls(devices, self._read_started)
            self._catalog = {key: value for key, value in self._catalog.items() if key in devices}
            results = await gather(*(self._with_catalog(device) for device in devices.values()), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        except AbleAuthError as err:
            self._action_polls.clear()
            self.update_interval = self._normal_interval
            raise ConfigEntryAuthFailed(str(err)) from None
        except AbleError as err:
            self._action_polls.clear()
            self.update_interval = self._normal_interval
            raise UpdateFailed(str(err)) from None

        registry = dr.async_get(self.hass)
        for result in results:
            registry.async_get_or_create(config_entry_id=self.entry.entry_id, **device_info(result))
        return {result.id: result for result in results}

    @callback
    def _async_refresh_finished(self) -> None:
        self._last_read = self._read_started
        for move in self._gate_moves.values():
            move.updated.set()

    async def async_shutdown(self) -> None:
        for move in self._gate_moves.values():
            move.cancel()
        await super().async_shutdown()

    def partial_position(self, device_id: str) -> int:
        return self.entry.options.get(CONF_PARTIAL_POSITIONS, {}).get(device_id, DEFAULT_PARTIAL_POSITION)

    @callback
    def async_set_partial_position(self, device_id: str, position: int) -> None:
        options = self.entry.options
        positions = {**options.get(CONF_PARTIAL_POSITIONS, {}), device_id: position}
        self.hass.config_entries.async_update_entry(self.entry, options={**options, CONF_PARTIAL_POSITIONS: positions})

    async def _async_gate_sample(
        self, device_id: str, move: GateMove, after: float, deadline: float,
    ) -> tuple[int, str]:
        """Wait for a finished read that was started after the given time."""
        while True:
            if move.cancelled:
                raise HomeAssistantError("The gate position action was cancelled")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise HomeAssistantError("Able did not report the expected gate movement in time")
            if self._last_read > after:
                device = self.data.get(device_id)
                if (not self.last_update_success or not device or device.position is None
                        or device.state is None or not device.supports_custom_position):
                    raise HomeAssistantError("Able did not provide a usable gate position and status")
                return device.position, device.state

            move.updated.clear()
            # Explicitly fetch during positioning if background polling is disabled in Home Assistant.
            interval = ACTIVE_POLL_INTERVAL.total_seconds()
            manual_poll = self.entry.pref_disable_polling and remaining > interval
            try:
                await wait_for(move.updated.wait(), interval if manual_poll else remaining)
            except TimeoutError:
                if manual_poll and not move.cancelled:
                    await self.async_refresh()

    async def _async_gate_command(self, device_id: str, move: GateMove, role: str) -> float:
        async with move.command:
            if move.cancelled:
                raise HomeAssistantError("The gate position action was cancelled")
            await self.async_run(device_id, role=role, _gate_move=move)
            return monotonic()

    async def _async_stopped_position(self, device_id: str, move: GateMove, after: float) -> int:
        deadline = after + ACTION_START_TIMEOUT
        previous = None
        while True:
            position, state = await self._async_gate_sample(device_id, move, after, deadline)
            after = self._last_read
            if state in MOVING_STATES:
                previous = None
            elif previous == position:
                return position
            else:
                previous = position

    async def async_move_to_partial_position(self, device_id: str) -> None:
        """Choose a direction from live position and verify where the gate stops."""
        if self._shutdown_requested:
            raise HomeAssistantError("The Able integration is shutting down")
        if device_id in self._gate_moves or any((device_id, stopping) in self._commands for stopping in (True, False)):
            raise HomeAssistantError("An Able command is already in progress for this gate")

        target = self.partial_position(device_id)
        move = self._gate_moves[device_id] = GateMove()
        self._set_poll_interval()
        stop_needed = False
        try:
            after = monotonic()
            await self.async_refresh()
            position, state = await self._async_gate_sample(device_id, move, after, monotonic() + ACTION_START_TIMEOUT)
            if state in MOVING_STATES:
                after = await self._async_gate_command(device_id, move, "stop")
                position = await self._async_stopped_position(device_id, move, after)
            if position == target:
                return

            direction = 1 if position < target else -1
            opposite = "closing" if direction == 1 else "opening"
            stop_needed = True
            after = await self._async_gate_command(device_id, move, "open" if direction == 1 else "close")
            deadline = after + ACTION_MAX_DURATION
            last_progress = after
            while True:
                current, state = await self._async_gate_sample(
                    device_id, move, after, min(deadline, last_progress + ACTION_START_TIMEOUT),
                )
                after = self._last_read
                if state == opposite or (current - position) * direction < 0:
                    stop_needed = False
                    raise HomeAssistantError("The gate moved away from the requested position")
                if current != position:
                    last_progress = monotonic()
                    position = current
                if (position - target) * direction < 0:
                    continue
                if target not in (0, 100):
                    stop_needed = False
                    after = await self._async_gate_command(device_id, move, "stop")
                    position = await self._async_stopped_position(device_id, move, after)
                elif state in MOVING_STATES:
                    continue
                stop_needed = False
                if position != target:
                    raise HomeAssistantError(f"The gate stopped at {position}% instead of the requested {target}%")
                return
        finally:
            try:
                if stop_needed and not move.cancelled:
                    try:
                        await self._async_gate_command(device_id, move, "stop")
                    except HomeAssistantError:
                        _LOGGER.warning("Able did not confirm Stop after the partial position action failed")
            finally:
                del self._gate_moves[device_id]
                self._set_poll_interval()

    async def async_run(
        self, device_id: str, *, role: str | None = None, action_id: str | None = None,
        position: int | None = None, relay: int | None = None, _gate_move: GateMove | None = None,
    ) -> None:
        """Run only a currently mapped action and preserve device-reported state."""
        device = self.data.get(device_id)
        if not device:
            raise HomeAssistantError("Able no longer lists this device")
        if action_id is None:
            action = device.action_for(role) if role else None
        else:
            action = next((item for item in device.buttons() if item.id == action_id), None)
        if action is None:
            raise HomeAssistantError("This action is unavailable for the Able device")
        stopping = action.role == "stop"
        active = self._gate_moves.get(device_id)
        manual_stop = active if active is not _gate_move else None
        if manual_stop and not stopping:
            raise HomeAssistantError("The gate is moving to a custom position. Use Stop to cancel it")
        if not self.last_update_success and not stopping:
            raise HomeAssistantError("Refresh the Able device state before sending another movement command")
        key = (device_id, stopping)
        if key in self._commands or (not stopping and (device_id, True) in self._commands):
            raise HomeAssistantError("An Able command is already being sent")
        cancelled = self._commands[key] = Event()
        if stopping and (motion := self._commands.get((device_id, False))):
            motion.set()
        params: dict[str, int] = {}
        if position is not None:
            params["parameter"] = position
        if relay is not None:
            params["rele"] = relay
        try:
            if manual_stop:
                manual_stop.cancel()
                # Let any Open or Close request in flight finish before sending Stop.
                async with manual_stop.command:
                    pass
            if action.role == "partial":
                latest = (await self.client.async_devices()).get(device_id)
                if latest is None or latest.state != "closed" or latest.position not in (None, 0):
                    raise HomeAssistantError("Partial Start is available when the gate is closed")
                device = latest
            if cancelled.is_set():
                raise HomeAssistantError("Stop cancelled the pending movement command")
            self._latest_command[device_id] = cancelled
            await self.client.async_perform(action.id, params)
        except AbleAuthError as err:
            await self.async_request_refresh()
            raise HomeAssistantError(str(err)) from None
        except AbleError as err:
            raise HomeAssistantError(str(err)) from None
        finally:
            self._commands.pop(key, None)
            is_latest = self._latest_command.get(device_id) is cancelled
            if is_latest:
                del self._latest_command[device_id]
        if is_latest and _gate_move is not None:
            self._action_polls.pop(device_id, None)
        elif is_latest:
            start_polling = not self._action_polls and not self._gate_moves
            self._action_polls[device_id] = ActionPoll(device, action.role, monotonic(), position, relay)
            self._set_poll_interval()
            if start_polling:
                await self.async_refresh()


type AbleConfigEntry = ConfigEntry[AbleCoordinator]
