"""One 'Vacuum <room>' and one 'Mop <room>' button per room, kept in sync with the map."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EufyCleanConfigEntry
from .const import DOMAIN, SIGNAL_ROOMS_UPDATED
from .coordinator import EufyCleanCoordinator
from .entity import EufyCleanEntity

ACTIONS = {"vacuum": ("Vacuum", "mdi:robot-vacuum"), "mop": ("Mop", "mdi:water")}


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyCleanConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    added: dict[str, RoomCleanButton] = {}

    @callback
    def sync_rooms() -> None:
        rooms = {r.id: r.name for r in coordinator.data.rooms}
        wanted = {f"{coordinator.device_sn}_room_{rid}_{action}" for rid in rooms for action in ACTIONS}

        # Drop buttons for rooms that no longer exist on the map.
        registry = er.async_get(hass)
        for unique_id in [u for u in added if u not in wanted]:
            added.pop(unique_id)
            if entity_id := registry.async_get_entity_id("button", DOMAIN, unique_id):
                registry.async_remove(entity_id)

        new: list[RoomCleanButton] = []
        for rid, name in rooms.items():
            for action in ACTIONS:
                unique_id = f"{coordinator.device_sn}_room_{rid}_{action}"
                if unique_id in added:
                    added[unique_id].rename(name)
                else:
                    added[unique_id] = RoomCleanButton(coordinator, rid, name, action)
                    new.append(added[unique_id])
        if new:
            async_add_entities(new)

    sync_rooms()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ROOMS_UPDATED.format(coordinator.device_sn), sync_rooms)
    )


class RoomCleanButton(EufyCleanEntity, ButtonEntity):
    def __init__(self, coordinator: EufyCleanCoordinator, room_id: int, room_name: str, action: str) -> None:
        super().__init__(coordinator)
        self._room_id = room_id
        self._action = action
        label, icon = ACTIONS[action]
        self._label = label
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.device_sn}_room_{room_id}_{action}"
        self._attr_name = f"{label} {room_name}"

    @callback
    def rename(self, room_name: str) -> None:
        name = f"{self._label} {room_name}"
        if name != self._attr_name:
            self._attr_name = name
            if self.hass:
                self.async_write_ha_state()

    async def async_press(self) -> None:
        await self.coordinator.async_clean_rooms([self._room_id], clean_type=self._action)
