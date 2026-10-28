"""Coordinator: owns the link to the robot (MQTT or Tuya cloud), its state and the room list."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import time
from typing import Any

from homeassistant.components.vacuum import VacuumActivity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import proto
from .api import EufyApiError, EufyAuthError, EufyCloud, EufyMqtt
from .const import (
    CONF_DEVICE_MODEL,
    CONF_DEVICE_SN,
    CONF_DOCK_HOST,
    CONF_OPENUDID,
    CONF_TRANSPORT,
    CONF_TUYA_REGION,
    DOMAIN,
    DPS_BATTERY,
    DPS_BATTERY_ALT,
    DPS_CLEAN_PARAM,
    DPS_ERROR,
    DPS_FAN_SPEED,
    DPS_FAN_SPEED_ALT,
    DPS_FIND_ROBOT,
    DPS_MODE_CTRL,
    DPS_ROOMS,
    DPS_WORK_STATUS,
    FAN_SPEEDS,
    SIGNAL_ROOMS_UPDATED,
    TRANSPORT_MQTT,
    TRANSPORT_TUYA,
    TUYA_REGION_ORDER,
    ZERO_BASED_ORDER_MODELS,
)
from .rooms_p2p import DockBusy, DockUnreachable, RoomsUnavailable, fetch_rooms, find_dock_host, probe_dock
from .transport import MqttTransport, Transport, TuyaTransport
from .tuya import TuyaCloud, TuyaError, async_tuya_login

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# A stored room table younger than this is used as-is at startup; older ones
# are refreshed over P2P (each P2P session costs the robot a session slot).
ROOMS_MAX_AGE = 24 * 3600
# Busy dock (its few local connections are taken): short retries, no fuss.
BUSY_RETRIES = 5
BUSY_RETRY_SECONDS = 60
# Unreachable dock: back off, raise a repair issue, keep the last known rooms.
UNREACHABLE_BACKOFF = (300, 1800)

_ALT_FAN = {"gentle": "Quiet", "normal": "Standard", "strong": "Turbo", "max": "Max"}


@dataclass
class RobotState:
    activity: VacuumActivity | None = None
    task: str = "unknown"
    battery: int | None = None
    fan_speed: str | None = None
    clean_type: str | None = None
    error_codes: list[int] = field(default_factory=list)
    map_id: int = 0
    rooms: list[proto.Room] = field(default_factory=list)
    active_room_ids: list[int] = field(default_factory=list)
    raw_dps: dict[str, Any] = field(default_factory=dict)


class EufyCleanCoordinator(DataUpdateCoordinator[RobotState]):
    """One coordinator per robot."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} {entry.title}", config_entry=entry,
            update_interval=MqttTransport.poll_interval,
        )
        self.device_sn: str = entry.data[CONF_DEVICE_SN]
        self.device_model: str = entry.data[CONF_DEVICE_MODEL]
        self.transport_kind: str = entry.data.get(CONF_TRANSPORT) or TRANSPORT_MQTT
        self.cloud = EufyCloud(
            async_get_clientsession(hass),
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            entry.data[CONF_OPENUDID],
        )
        self.mqtt: EufyMqtt | None = None
        self.tuya: TuyaCloud | None = None
        self.transport: Transport | None = None
        self.connected = False
        self.rooms_task: asyncio.Task[None] | None = None
        self._local_key = ""
        self._dock_host: str | None = None
        self._rooms_fetched_at = 0.0
        self._rooms_source: str | None = None  # "p2p" or "dps": where the room list came from
        self._rooms_lock = asyncio.Lock()
        self._p2p_failures = 0
        self._retry_unsub: CALLBACK_TYPE | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{self.device_sn}"
        )
        self.data = RobotState()

    # ------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        """Log in, load saved rooms, seed state from the cloud, open the transport."""
        if saved := await self._store.async_load():
            self._dock_host = saved.get("dock_host")
            self._rooms_source = saved.get("source")
            # On Tuya robots only a P2P room list carries the local map id the
            # firmware accepts; anything else (0.2.0/0.2.1 saved the cloud
            # snapshot) is dropped and fetched again.
            if self.transport_kind != TRANSPORT_TUYA or self._rooms_source == "p2p":
                self.data.map_id = saved.get("map_id", 0)
                self.data.rooms = [proto.Room(r["id"], r["name"]) for r in saved.get("rooms", [])]
                self._rooms_fetched_at = float(saved.get("fetched_at") or 0)

        try:
            await self.cloud.login()
            if self.transport_kind == TRANSPORT_TUYA:
                await self._async_start_tuya()
            else:
                await self._async_start_mqtt()
        except EufyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EufyApiError as err:
            raise UpdateFailed(str(err)) from err
        assert self.transport is not None
        self.update_interval = self.transport.poll_interval
        self.async_set_updated_data(self.data)

    async def _async_start_mqtt(self) -> None:
        device = await self.cloud.get_device(self.device_sn)
        creds = await self.cloud.get_mqtt_credentials()
        if device is None:
            raise UpdateFailed(
                f"{self.device_sn} is not in the Eufy MQTT device list; this robot "
                "may only be reachable through Tuya, which this integration does not use"
            )

        self.mqtt = EufyMqtt(
            creds, self.cloud.openudid, self.device_sn, self.device_model,
            self._handle_dps, self._handle_connection,
        )
        # Shared robots must be addressed with the owner's account id.
        member = device.get("member") or {}
        owner = member.get("admin_user_id") or member.get("member_user_id")
        if owner and owner != self.mqtt.user_id:
            _LOGGER.debug("Robot is shared; addressing commands as owner %s", owner)
            self.mqtt.user_id = owner

        if dps := device.get("dps"):
            self._apply_dps(dps)
        self.transport = MqttTransport(self.mqtt, self.cloud, self.device_sn)
        await self.transport.start()

    async def _async_start_tuya(self) -> None:
        if not self.cloud.user_id:
            raise UpdateFailed("Eufy login returned no user id; cannot log in to the Tuya cloud")
        entry = self.config_entry
        stored_region = entry.data.get(CONF_TUYA_REGION)
        session = async_get_clientsession(self.hass)
        try:
            try:
                regions = (stored_region,) if stored_region else TUYA_REGION_ORDER
                self.tuya = await async_tuya_login(session, self.cloud.user_id, regions)
            except TuyaError as err:
                if not stored_region or err.code == "NETWORK":
                    raise
                # The stored region stopped working: probe them all once more.
                _LOGGER.info("Tuya login in stored region %s failed (%s); trying all regions", stored_region, err)
                self.tuya = await async_tuya_login(session, self.cloud.user_id, TUYA_REGION_ORDER)
            devices = await self.tuya.get_devices()
        except TuyaError as err:
            raise UpdateFailed(f"Tuya cloud: {err}") from err
        except (KeyError, TypeError, ValueError) as err:
            raise UpdateFailed(f"Tuya cloud returned an unexpected response: {err!r}") from err
        if self.tuya.region != stored_region:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_TUYA_REGION: self.tuya.region}
            )
        device = next((d for d in devices if d.get("devId") == self.device_sn), None)
        if device is None:
            raise UpdateFailed(f"{self.device_sn} is not in the Tuya device list for this account")
        self._local_key = device.get("localKey") or ""
        if dps := device.get("dps"):
            self._apply_dps(dps)
        self.transport = TuyaTransport(self.tuya, self.device_sn, self._async_tuya_relogin)
        await self.transport.start()
        self.connected = True
        if not self.data.rooms or time.time() - self._rooms_fetched_at > ROOMS_MAX_AGE:
            self._start_rooms_task()

    async def _async_tuya_relogin(self) -> None:
        assert self.tuya is not None and self.cloud.user_id
        await self.tuya.login(self.cloud.user_id)

    async def async_stop(self) -> None:
        if self._retry_unsub:
            self._retry_unsub()
            self._retry_unsub = None
        if self.rooms_task and not self.rooms_task.done():
            self.rooms_task.cancel()
        if self.transport:
            await self.transport.stop()

    async def _async_update_data(self) -> RobotState:
        """Poll the cloud's last-known DPS snapshot (the only updates on Tuya; backfill on MQTT)."""
        assert self.transport is not None
        try:
            dps = await self.transport.poll()
        except EufyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EufyApiError as err:
            raise UpdateFailed(str(err)) from err
        if dps:
            self._apply_dps(dps)
        return self.data

    # ------------------------------------------------------------ incoming

    @callback
    def _handle_connection(self, connected: bool) -> None:
        self.connected = connected
        self.async_update_listeners()

    @callback
    def _handle_dps(self, dps: dict[str, Any]) -> None:
        self._apply_dps(dps)
        self.async_set_updated_data(self.data)

    def _apply_dps(self, dps: dict[str, Any]) -> None:
        state = self.data
        state.raw_dps.update(dps)

        if isinstance(value := dps.get(DPS_WORK_STATUS), str):
            if ws := proto.parse_work_status(value):
                state.activity, state.task = _activity(ws)
                if ws.mode != 1 or ws.state not in (proto.WS_CLEANING, proto.WS_POSITIONING):
                    if state.activity != VacuumActivity.PAUSED:
                        state.active_room_ids = []

        for key in (DPS_BATTERY, DPS_BATTERY_ALT):
            if key in dps:
                try:
                    state.battery = int(dps[key])
                    break
                except (TypeError, ValueError):
                    pass

        if (fan := dps.get(DPS_FAN_SPEED)) is not None:
            state.fan_speed = _fan_name(fan)
        elif (fan := dps.get(DPS_FAN_SPEED_ALT)) is not None:
            state.fan_speed = _ALT_FAN.get(str(fan).lower(), state.fan_speed)

        if isinstance(value := dps.get(DPS_CLEAN_PARAM), str):
            state.clean_type = proto.parse_clean_type(value) or state.clean_type

        if DPS_ERROR in dps:
            state.error_codes = proto.parse_error_code(dps[DPS_ERROR])

        # On Tuya robots the cloud's copy of DPS 165 is not the local room table
        # (an S1 Pro reports one placeholder room on the cloud map id); the
        # dock's P2P map stream is the only room source there.
        if self.transport_kind != TRANSPORT_TUYA and isinstance(value := dps.get(DPS_ROOMS), str):
            if (table := proto.parse_room_table(value)) and table.rooms:
                self._set_rooms(table, "dps")

    def _set_rooms(self, table: proto.RoomTable, source: str) -> None:
        state = self.data
        new = [(r.id, r.name) for r in table.rooms]
        self._rooms_fetched_at = time.time()
        self._rooms_source = source
        if new == [(r.id, r.name) for r in state.rooms] and table.map_id == state.map_id:
            self._save_rooms()
            return
        _LOGGER.info("Eufy room list for %s: %s", self.device_sn, [n for _, n in new])
        state.rooms, state.map_id = table.rooms, table.map_id
        self._save_rooms()
        async_dispatcher_send(self.hass, SIGNAL_ROOMS_UPDATED.format(self.device_sn))

    def _save_rooms(self) -> None:
        state = self.data
        self._store.async_delay_save(
            lambda: {
                "map_id": state.map_id,
                "rooms": [{"id": r.id, "name": r.name} for r in state.rooms],
                "fetched_at": self._rooms_fetched_at,
                "source": self._rooms_source,
                "dock_host": self._dock_host,
            },
            1,
        )

    # ------------------------------------------------------------ rooms over P2P

    @property
    def _issue_id(self) -> str:
        return f"dock_unreachable_{self.device_sn}"

    @callback
    def _start_rooms_task(self, *_: Any) -> None:
        """Run one P2P room refresh in the background; a running one is left alone."""
        if self._retry_unsub:
            self._retry_unsub()
            self._retry_unsub = None
        if self.rooms_task and not self.rooms_task.done():
            return
        self.rooms_task = self.config_entry.async_create_background_task(
            self.hass, self._async_refresh_rooms_p2p(), f"{DOMAIN} rooms {self.device_sn}"
        )

    async def _async_refresh_rooms_p2p(self) -> None:
        """One P2P session to the dock for the room table. Never raises."""
        if self._rooms_lock.locked():
            return
        async with self._rooms_lock:
            try:
                await self._fetch_rooms_once()
            except Exception:  # noqa: BLE001 - a room refresh must never take the entry down
                _LOGGER.exception("Unexpected error reading the room list over P2P")

    async def _probe(self, host: str) -> str:
        """'ok', 'busy' (after the retries) or 'unreachable'."""
        for attempt in range(BUSY_RETRIES + 1):
            try:
                await probe_dock(host)
                return "ok"
            except DockBusy:
                if attempt == BUSY_RETRIES:
                    return "busy"
                await asyncio.sleep(BUSY_RETRY_SECONDS)
            except DockUnreachable:
                return "unreachable"
        return "busy"

    async def _fetch_rooms_once(self) -> None:
        assert self.tuya is not None
        configured = self.config_entry.data.get(CONF_DOCK_HOST)
        host = configured or self._dock_host
        if not host:
            host = await find_dock_host(self.device_sn)
            if not host:
                self._dock_unreachable("the dock did not answer discovery on this network")
                return
            self._dock_host = host
        # The dock must be taking connections before the cloud reserves a session slot.
        result = await self._probe(host)
        if result != "ok" and not configured:
            # A remembered address may be stale (DHCP moved the dock, or another
            # device now answers there); ask the network once before giving up.
            fresh = await find_dock_host(self.device_sn)
            if fresh and fresh != host:
                _LOGGER.info("Dock of %s moved from %s to %s", self.config_entry.title, host, fresh)
                host = self._dock_host = fresh
                result = await self._probe(host)
        if result == "busy":
            _LOGGER.info("Dock %s is busy (its local connections are in use); room list not refreshed", host)
            return
        if result == "unreachable":
            self._dock_unreachable(f"{host} does not answer")
            return
        try:
            rtc = await self.tuya.get_rtc_config(self.device_sn)
        except TuyaError as err:
            _LOGGER.warning("Could not get P2P credentials from the Tuya cloud: %s", err)
            return
        if not rtc or not rtc.get("device_password"):
            _LOGGER.warning("Tuya cloud returned no P2P credentials for %s", self.device_sn)
            return
        try:
            table = await fetch_rooms(
                host=host, local_key=self._local_key, device_id=self.device_sn, rtc=rtc
            )
        except DockBusy as err:
            _LOGGER.info("Dock %s refused the P2P session: %s", host, err)
            return
        except RoomsUnavailable as err:
            _LOGGER.warning("No room list from %s: %s", host, err)
            return
        self._p2p_failures = 0
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._set_rooms(table, "p2p")

    def _dock_unreachable(self, reason: str) -> None:
        self._p2p_failures += 1
        _LOGGER.warning(
            "Can't reach the dock of %s to read its rooms (%s); keeping the last known list",
            self.config_entry.title, reason,
        )
        # Try again later, a bounded number of times; after that it's up to Refresh rooms.
        if self._p2p_failures <= len(UNREACHABLE_BACKOFF):
            backoff = UNREACHABLE_BACKOFF[self._p2p_failures - 1]
            self._retry_unsub = async_call_later(self.hass, backoff, self._start_rooms_task)
        ir.async_create_issue(
            self.hass, DOMAIN, self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="dock_unreachable",
            translation_placeholders={"name": self.config_entry.title},
        )

    # ------------------------------------------------------------ outgoing

    async def async_send(self, dps: dict[str, Any]) -> None:
        if not self.transport:
            raise HomeAssistantError("Eufy robot is not connected")
        try:
            await self.transport.send(dps)
        except EufyApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_mode(self, method: int) -> None:
        await self.async_send({DPS_MODE_CTRL: proto.mode_ctrl(method)})

    async def async_set_fan_speed(self, fan_speed: str) -> None:
        await self.async_send({DPS_FAN_SPEED: _fan_wire(fan_speed, self.data.raw_dps)})

    async def async_locate(self) -> None:
        await self.async_send({DPS_FIND_ROBOT: True})

    def resolve_rooms(self, wanted: list[str | int]) -> list[int]:
        """Turn room names (case-insensitive) or ids into ids, keeping order."""
        by_name = {r.name.casefold(): r.id for r in self.data.rooms}
        by_id = {r.id for r in self.data.rooms}
        ids: list[int] = []
        unknown: list[str] = []
        for item in wanted:
            text = str(item).strip()
            if text.isdigit() and int(text) in by_id:
                ids.append(int(text))
            elif text.casefold() in by_name:
                ids.append(by_name[text.casefold()])
            else:
                unknown.append(text)
        if unknown:
            known = ", ".join(r.name for r in self.data.rooms) or "none yet"
            raise HomeAssistantError(f"Unknown room(s): {', '.join(unknown)}. Known rooms: {known}")
        return list(dict.fromkeys(ids))

    async def async_clean_rooms(
        self,
        room_ids: list[int],
        clean_type: str | None = None,
        fan_speed: str | None = None,
        mop_level: str | None = None,
        clean_times: int = 1,
    ) -> None:
        """Clean the given rooms, optionally overriding vacuum/mop and suction."""
        if not room_ids:
            raise HomeAssistantError("No rooms given")
        if not self.data.rooms:
            if self.transport_kind == TRANSPORT_TUYA:
                raise HomeAssistantError(
                    "No room list from the robot yet; check Home Assistant can reach the dock "
                    "on your network, then run Refresh rooms"
                )
            raise HomeAssistantError(
                "No room list received from the robot yet; open the map in the Eufy app once"
            )
        if clean_type or mop_level or (fan_speed and _uses_param_fan(self.data.raw_dps)):
            await self.async_send(
                {
                    DPS_CLEAN_PARAM: proto.clean_param_request(
                        clean_type=clean_type,
                        suction=fan_speed if _uses_param_fan(self.data.raw_dps) else None,
                        mop_level=mop_level,
                    )
                }
            )
            await asyncio.sleep(1)
        if fan_speed and not _uses_param_fan(self.data.raw_dps):
            await self.async_set_fan_speed(fan_speed)
            await asyncio.sleep(1)
        if self.transport_kind == TRANSPORT_TUYA:
            # The firmware wants the local map id (1 = first saved map, from P2P)
            # and, on the S1 Pro, zero-based room order slots.
            payload = proto.select_rooms_clean(
                room_ids, self.data.map_id or 1, clean_times,
                zero_based_order=self.device_model.startswith(ZERO_BASED_ORDER_MODELS),
            )
        else:
            payload = proto.select_rooms_clean(room_ids, self.data.map_id, clean_times)
        await self.async_send({DPS_MODE_CTRL: payload})
        self.data.active_room_ids = room_ids
        self.async_update_listeners()

    async def async_refresh_rooms(self) -> None:
        """Re-read the rooms: a P2P session on Tuya robots, the cloud snapshot on MQTT."""
        if self.transport_kind == TRANSPORT_TUYA:
            self._p2p_failures = 0  # a manual refresh restarts the retry ladder
            self._start_rooms_task()
            return
        await self.async_request_refresh()


def _activity(ws: proto.WorkStatus) -> tuple[VacuumActivity, str]:
    s = ws.state
    if s == proto.WS_FAULT:
        return VacuumActivity.ERROR, "error"
    if s == proto.WS_CHARGING:
        return VacuumActivity.DOCKED, "charged" if ws.charging_state == 1 else "charging"
    if s == proto.WS_GO_HOME:
        return (VacuumActivity.PAUSED, "paused") if ws.paused else (VacuumActivity.RETURNING, "returning")
    if s in (proto.WS_CLEANING, proto.WS_POSITIONING, proto.WS_REMOTE, proto.WS_CRUISING):
        if ws.go_wash_mode in (1, 2):
            return VacuumActivity.DOCKED, "washing mop" if ws.go_wash_mode == 1 else "drying mop"
        if ws.paused:
            return VacuumActivity.PAUSED, "paused"
        task = {1: "room cleaning", 2: "zone cleaning", 3: "spot cleaning"}.get(ws.mode, "cleaning")
        if s == proto.WS_POSITIONING:
            task = "positioning"
        return VacuumActivity.CLEANING, task
    if s == proto.WS_SLEEP:
        return VacuumActivity.IDLE, "sleeping"
    return VacuumActivity.IDLE, "standby"


def _fan_name(value: Any) -> str | None:
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        idx = int(value)
        return FAN_SPEEDS[idx] if 0 <= idx < len(FAN_SPEEDS) else None
    text = str(value).strip().lower()
    return next((s for s in FAN_SPEEDS if s.lower() == text), None)


def _fan_wire(fan_speed: str, raw: dict[str, Any]) -> Any:
    """S1 Pro reports DPS 158 as a word ('Quiet'); X-series as an index."""
    current = raw.get(DPS_FAN_SPEED)
    if isinstance(current, str) and not current.isdigit():
        return fan_speed
    return FAN_SPEEDS.index(fan_speed)


def _uses_param_fan(raw: dict[str, Any]) -> bool:
    """X10 and later carry suction inside CleanParam instead of DPS 158."""
    return DPS_FAN_SPEED not in raw and DPS_CLEAN_PARAM in raw
