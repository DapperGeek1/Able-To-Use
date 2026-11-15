"""Position, motion state, and diagnostics reported by Able."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AbleConfigEntry, AbleCoordinator
from .entity import AbleEntity, async_discover
from .models import COVER_TYPES, SUNSHADING, Device

DESCRIPTIONS = (
    SensorEntityDescription(key="state", translation_key="gate_state", device_class=SensorDeviceClass.ENUM,
                            options=["closed", "open", "partial", "opening", "closing", "moving"]),
    SensorEntityDescription(key="position", translation_key="position", native_unit_of_measurement=PERCENTAGE),
    SensorEntityDescription(key="favorite", translation_key="favorite", native_unit_of_measurement=PERCENTAGE,
                            entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="last_activity", translation_key="last_activity", device_class=SensorDeviceClass.TIMESTAMP,
                            entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="firmware", translation_key="firmware", entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry: AbleConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_discover(entry, async_add_entities, _entities)


def _entities(coordinator: AbleCoordinator, device: Device) -> list[AbleEntity]:
    keys = {"last_activity", "firmware"}
    if device.kind in COVER_TYPES:
        keys |= {"state", "position"}
    if device.kind == SUNSHADING or device.favorite is not None:
        keys.add("favorite")
    return [AbleSensor(coordinator, device, desc) for desc in DESCRIPTIONS if desc.key in keys]


class AbleSensor(AbleEntity, SensorEntity):
    """Expose a reported value without interpolating device motion."""

    def __init__(self, coordinator: AbleCoordinator, device: Device, desc: SensorEntityDescription) -> None:
        super().__init__(coordinator, device, f"sensor:{desc.key}")
        self.entity_description = desc

    @property
    def native_value(self) -> str | int | datetime | None:
        return getattr(self.device, self.entity_description.key, None)
