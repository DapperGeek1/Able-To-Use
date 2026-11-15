"""Polling, device discovery, and account-scoped command dispatch."""

from asyncio import Event, Semaphore, gather
from dataclasses import dataclass, field, replace
from datetime import timedelta
import logging
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AbleAuthError, AbleClient, AbleError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
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

    def _finish_action_polls(self, devices: dict[str, Device], read_started: float) -> None:
        now = monotonic()
        for device_id, action in list(self._action_polls.items()):
            if read_started < action.started:
                continue
            if (device := devices.get(device_id)) is None or action.complete(device, now):
                del self._action_polls[device_id]
        self.update_interval = ACTIVE_POLL_INTERVAL if self._action_polls else self._normal_interval

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
        read_started = monotonic()
        try:
            devices = await self.client.async_devices()
            self._finish_action_polls(devices, read_started)
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

    async def async_run(
        self, device_id: str, *, role: str | None = None, action_id: str | None = None,
        position: int | None = None, relay: int | None = None,
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
        if not self.last_update_success and not stopping:
            raise HomeAssistantError("Refresh the Able device state before sending another movement command")
        key = (device_id, stopping)
        if key in self._commands:
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
        if is_latest:
            start_polling = not self._action_polls
            self._action_polls[device_id] = ActionPoll(device, action.role, monotonic(), position, relay)
            self.update_interval = ACTIVE_POLL_INTERVAL
            if start_polling:
                await self.async_refresh()


type AbleConfigEntry = ConfigEntry[AbleCoordinator]
