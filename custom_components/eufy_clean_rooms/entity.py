"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import EufyCleanCoordinator


class EufyCleanEntity(CoordinatorEntity[EufyCleanCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EufyCleanCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_sn)},
            name=coordinator.config_entry.data[CONF_DEVICE_NAME],
            manufacturer="eufy",
            model=coordinator.device_model,
            serial_number=coordinator.device_sn,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.connected
