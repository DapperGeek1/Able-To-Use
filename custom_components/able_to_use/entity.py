"""Shared entity lifecycle and discovery."""

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import AbleConfigEntry, AbleCoordinator, device_info
from .models import Device


class AbleEntity(CoordinatorEntity[AbleCoordinator]):
    """An entity attached to an Able device in the current account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AbleCoordinator, device: Device, suffix: str) -> None:
        super().__init__(coordinator)
        self._device_id = device.id
        self._attr_unique_id = f"{device.id}:{suffix}"
        self._attr_device_info: DeviceInfo = device_info(device)

    @property
    def device(self) -> Device | None:
        return self.coordinator.data.get(self._device_id)

    @property
    def available(self) -> bool:
        return super().available and self.device is not None


@callback
def async_discover(
    entry: AbleConfigEntry, add_entities: AddEntitiesCallback,
    factory: Callable[[AbleCoordinator, Device], Iterable[AbleEntity]],
) -> None:
    """Add newly returned devices and capabilities during polling."""
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def discover() -> None:
        entities = []
        for device in coordinator.data.values():
            for entity in factory(coordinator, device):
                if entity.unique_id is not None and entity.unique_id not in added:
                    added.add(entity.unique_id)
                    entities.append(entity)
        if entities:
            add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()
