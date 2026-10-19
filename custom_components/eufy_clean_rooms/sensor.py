"""Battery, task and room-list sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EufyCleanConfigEntry
from .entity import EufyCleanEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyCleanConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities([BatterySensor(c), TaskSensor(c), RoomsSensor(c), ErrorSensor(c)])


class BatterySensor(EufyCleanEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_battery"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.battery


class TaskSensor(EufyCleanEntity, SensorEntity):
    _attr_translation_key = "task"
    _attr_icon = "mdi:robot-vacuum-variant"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_task"

    @property
    def native_value(self) -> str:
        return self.coordinator.data.task


class RoomsSensor(EufyCleanEntity, SensorEntity):
    _attr_translation_key = "rooms"
    _attr_icon = "mdi:floor-plan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_rooms"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.rooms)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {"map_id": data.map_id, "rooms": {r.id: r.name for r in data.rooms}}


class ErrorSensor(EufyCleanEntity, SensorEntity):
    _attr_translation_key = "error"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_error"

    @property
    def native_value(self) -> int:
        codes = self.coordinator.data.error_codes
        return codes[0] if codes else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"codes": self.coordinator.data.error_codes}
