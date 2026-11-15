"""Home Assistant cover entities backed by Able's reported state."""

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AbleConfigEntry, AbleCoordinator
from .entity import AbleEntity, async_discover
from .models import COVER_TYPES, GATE, Device

ROLLER_CLASSES = {
    "BLINDS": CoverDeviceClass.BLIND, "DOOR": CoverDeviceClass.DOOR,
    "AWNING": CoverDeviceClass.AWNING, "SLAT_ROLLING": CoverDeviceClass.SHUTTER,
    "LOUVER": CoverDeviceClass.BLIND,
}
FEATURES = {
    "open": CoverEntityFeature.OPEN, "close": CoverEntityFeature.CLOSE,
    "stop": CoverEntityFeature.STOP, "target": CoverEntityFeature.SET_POSITION,
}


async def async_setup_entry(hass: HomeAssistant, entry: AbleConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_discover(entry, async_add_entities, _entities)


def _entities(coordinator: AbleCoordinator, device: Device) -> list[AbleEntity]:
    return [AbleCover(coordinator, device)] if device.kind in COVER_TYPES else []


class AbleCover(AbleEntity, CoverEntity):
    """A gate or sun-shading controller with independent direction commands."""

    _attr_name = None

    def __init__(self, coordinator: AbleCoordinator, device: Device) -> None:
        super().__init__(coordinator, device, "cover")
        self._attr_device_class = (CoverDeviceClass.GATE if device.kind == GATE
                                   else ROLLER_CLASSES.get(device.subtype, CoverDeviceClass.SHUTTER))

    @property
    def supported_features(self) -> CoverEntityFeature:
        features = CoverEntityFeature(0)
        if device := self.device:
            for role, flag in FEATURES.items():
                if device.action_for(role):
                    features |= flag
        return features

    @property
    def current_cover_position(self) -> int | None:
        return self.device.position if self.device else None

    @property
    def is_closed(self) -> bool | None:
        return self.device.state == "closed" if self.device and self.device.state is not None else None

    @property
    def is_opening(self) -> bool | None:
        return self.device.state == "opening" if self.device and self.device.state is not None else None

    @property
    def is_closing(self) -> bool | None:
        return self.device.state == "closing" if self.device and self.device.state is not None else None

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self._device_id, role="open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self._device_id, role="close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self._device_id, role="stop")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self._device_id, role="target", position=kwargs[ATTR_POSITION])
