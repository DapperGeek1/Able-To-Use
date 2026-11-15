"""Binary sensors from reported movement and relay outputs."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AbleConfigEntry, AbleCoordinator
from .entity import AbleEntity, async_discover
from .models import COVER_TYPES, Device


async def async_setup_entry(hass: HomeAssistant, entry: AbleConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_discover(entry, async_add_entities, _entities)


def _entities(coordinator: AbleCoordinator, device: Device) -> list[AbleEntity]:
    entities: list[AbleEntity] = []
    if device.kind in COVER_TYPES:
        entities.append(AbleMoving(coordinator, device, "moving"))
    entities.extend(AbleRelayState(coordinator, device, channel) for channel, value in enumerate(device.relays, 1)
                    if value is not None)
    return entities


class AbleMoving(AbleEntity, BinarySensorEntity):
    """Report whether Able says the cover is travelling."""

    _attr_device_class = BinarySensorDeviceClass.MOVING
    _attr_translation_key = "moving"

    @property
    def is_on(self) -> bool | None:
        return self.device.state in {"moving", "opening", "closing"} if self.device and self.device.state else None


class AbleRelayState(AbleEntity, BinarySensorEntity):
    """Read the physical relay state where the API supplies it."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "relay"

    def __init__(self, coordinator: AbleCoordinator, device: Device, channel: int) -> None:
        super().__init__(coordinator, device, f"relay:{channel}")
        self._channel = channel
        self._attr_translation_placeholders = {"channel": str(channel)}

    @property
    def is_on(self) -> bool | None:
        return self.device.relays[self._channel - 1] if self.device else None
