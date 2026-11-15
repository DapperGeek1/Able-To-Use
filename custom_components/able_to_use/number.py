"""Per-gate position settings for the Home Assistant action."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AbleConfigEntry, AbleCoordinator
from .entity import AbleEntity, async_discover
from .models import Device


async def async_setup_entry(hass: HomeAssistant, entry: AbleConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_discover(entry, async_add_entities, _entities)


def _entities(coordinator: AbleCoordinator, device: Device) -> list[AbleEntity]:
    return [AblePartialPosition(coordinator, device)] if device.supports_custom_position else []


class AblePartialPosition(AbleEntity, NumberEntity):
    """Store the requested position without sending a movement command."""

    _attr_translation_key = "partial_open_percentage"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: AbleCoordinator, device: Device) -> None:
        super().__init__(coordinator, device, "partial_open_percentage")

    @property
    def native_value(self) -> int:
        return self.coordinator.partial_position(self._device_id)

    async def async_set_native_value(self, value: float) -> None:
        if not (0 <= value <= 100 and float(value).is_integer()):
            raise HomeAssistantError("Choose a whole percentage between 0 and 100")
        self.coordinator.async_set_partial_position(self._device_id, int(value))
        self.async_write_ha_state()
