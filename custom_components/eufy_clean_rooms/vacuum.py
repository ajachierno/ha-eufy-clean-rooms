"""Vacuum entity, including HA's native clean-area (room segment) support."""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EufyCleanConfigEntry, proto
from .const import FAN_SPEEDS, SIGNAL_ROOMS_UPDATED
from .entity import EufyCleanEntity

FEATURES = (
    VacuumEntityFeature.START
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.LOCATE
    | VacuumEntityFeature.FAN_SPEED
    | VacuumEntityFeature.STATE
    | VacuumEntityFeature.SEND_COMMAND
    | VacuumEntityFeature.CLEAN_SPOT
    | VacuumEntityFeature.CLEAN_AREA
)


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyCleanConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([EufyCleanVacuum(entry.runtime_data)])


class EufyCleanVacuum(EufyCleanEntity, StateVacuumEntity):
    _attr_name = None
    _attr_supported_features = FEATURES
    _attr_fan_speed_list = FAN_SPEEDS

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.device_sn
        self._attr_activity = coordinator.data.activity
        self._attr_fan_speed = coordinator.data.fan_speed

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ROOMS_UPDATED.format(self.coordinator.device_sn),
                self._rooms_updated,
            )
        )

    @callback
    def _rooms_updated(self) -> None:
        """Raise HA's 'segments changed' repair if rooms differ from the user's area mapping."""
        last_seen = self.last_seen_segments
        if last_seen is not None and {(s.id, s.name) for s in last_seen} != {
            (s.id, s.name) for s in self._segments()
        }:
            self.async_create_segments_issue()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        self._attr_activity = data.activity
        self._attr_fan_speed = data.fan_speed
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        names = {r.id: r.name for r in data.rooms}
        return {
            "task": data.task,
            "map_id": data.map_id,
            "rooms": [{"id": r.id, "name": r.name} for r in data.rooms],
            "cleaning_rooms": [names.get(i, f"Room {i}") for i in data.active_room_ids],
            "error_codes": data.error_codes,
        }

    def _segments(self) -> list[Segment]:
        return [Segment(id=str(r.id), name=r.name) for r in self.coordinator.data.rooms]

    async def async_get_segments(self) -> list[Segment]:
        return self._segments()

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        await self.coordinator.async_clean_rooms([int(s) for s in segment_ids if s.isdigit()])

    async def async_start(self) -> None:
        method = proto.RESUME_TASK if self.activity == "paused" else proto.START_AUTO_CLEAN
        await self.coordinator.async_mode(method)

    async def async_pause(self) -> None:
        await self.coordinator.async_mode(proto.PAUSE_TASK)

    async def async_stop(self, **kwargs: Any) -> None:
        await self.coordinator.async_mode(proto.STOP_TASK)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self.coordinator.async_mode(proto.START_GOHOME)

    async def async_clean_spot(self, **kwargs: Any) -> None:
        await self.coordinator.async_mode(proto.START_SPOT_CLEAN)

    async def async_locate(self, **kwargs: Any) -> None:
        await self.coordinator.async_locate()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        await self.coordinator.async_set_fan_speed(fan_speed)

    async def async_send_command(
        self, command: str, params: dict[str, Any] | list[Any] | None = None, **kwargs: Any
    ) -> None:
        """`room_clean` with params {rooms: [...], clean_type, fan_speed, mop_level}."""
        params = params if isinstance(params, dict) else {"rooms": params or []}
        if command in ("room_clean", "clean_rooms"):
            await self.coordinator.async_clean_rooms(
                self.coordinator.resolve_rooms(params.get("rooms") or params.get("room_ids") or []),
                clean_type=params.get("clean_type"),
                fan_speed=params.get("fan_speed"),
                mop_level=params.get("mop_level"),
                clean_times=int(params.get("clean_times", 1)),
            )
        elif command == "refresh_rooms":
            await self.coordinator.async_refresh_rooms()
        else:
            raise ValueError(f"Unsupported command {command}")
