"""Buttons for actions Able makes available on each controller."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AbleConfigEntry, AbleCoordinator
from .entity import AbleEntity, async_discover
from .models import Action, Device


async def async_setup_entry(hass: HomeAssistant, entry: AbleConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_discover(entry, async_add_entities, _entities)


def _entities(coordinator: AbleCoordinator, device: Device) -> list[AbleEntity]:
    buttons: list[AbleEntity] = [AbleButton(coordinator, device, action) for action in device.buttons()]
    if device.supports_custom_position:
        buttons.append(AbleMoveToPartialPosition(coordinator, device))
    return buttons


class AbleButton(AbleEntity, ButtonEntity):
    """Press one named control from the device's Able action list."""

    def __init__(self, coordinator: AbleCoordinator, device: Device, action: Action) -> None:
        super().__init__(coordinator, device, f"action:{action.id}")
        self._action_id = action.id
        self._role = action.role
        self._attr_name = action.name

    @property
    def available(self) -> bool:
        device = self.device
        return bool(device and (super().available or self._role == "stop")
                    and any(action.id == self._action_id for action in device.buttons()))

    async def async_press(self) -> None:
        await self.coordinator.async_run(self._device_id, action_id=self._action_id)


class AbleMoveToPartialPosition(AbleEntity, ButtonEntity):
    """Move in either direction toward the percentage set in Home Assistant."""

    _attr_translation_key = "move_to_partial_position"

    def __init__(self, coordinator: AbleCoordinator, device: Device) -> None:
        super().__init__(coordinator, device, "move_to_partial_position")

    @property
    def available(self) -> bool:
        device = self.device
        return bool(super().available and device and device.supports_custom_position
                    and device.position is not None and device.state is not None)

    async def async_press(self) -> None:
        position = self.coordinator.partial_position(self._device_id)
        await self.coordinator.async_set_gate_position(self._device_id, position)
