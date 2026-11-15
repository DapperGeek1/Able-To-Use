"""On/off entities for Able's single and dual light controllers."""

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AbleConfigEntry, AbleCoordinator
from .entity import AbleEntity, async_discover
from .models import DUAL_LIGHT, LIGHT, Device


async def async_setup_entry(hass: HomeAssistant, entry: AbleConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_discover(entry, async_add_entities, _entities)


def _entities(coordinator: AbleCoordinator, device: Device) -> list[AbleEntity]:
    if device.kind not in (LIGHT, DUAL_LIGHT) or not (device.action_for("on") and device.action_for("off")):
        return []
    return [AbleLight(coordinator, device, relay) for relay in ((1, 2) if device.kind == DUAL_LIGHT else (1,))]


class AbleLight(AbleEntity, LightEntity):
    """A relay channel that the Android app exposes as a light."""

    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, coordinator: AbleCoordinator, device: Device, relay: int) -> None:
        super().__init__(coordinator, device, f"light:{relay}")
        self._relay = relay
        if device.kind == DUAL_LIGHT:
            self._attr_translation_key = "channel"
            self._attr_translation_placeholders = {"channel": str(relay)}
        else:
            self._attr_name = None

    @property
    def available(self) -> bool:
        return bool(super().available and self.device and self.device.action_for("on") and self.device.action_for("off"))

    @property
    def is_on(self) -> bool | None:
        return self.device.relays[self._relay - 1] if self.device else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self._device_id, role="on", relay=self._relay)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self._device_id, role="off", relay=self._relay)
