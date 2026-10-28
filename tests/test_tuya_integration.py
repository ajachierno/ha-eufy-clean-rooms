"""End-to-end tests for a Tuya-cloud robot, with the Tuya cloud and the P2P channel mocked."""

from __future__ import annotations

from datetime import timedelta
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.eufy_clean_rooms import proto
from custom_components.eufy_clean_rooms.const import DOMAIN
from custom_components.eufy_clean_rooms.rooms_p2p import DockBusy, DockUnreachable

SN = "ebed0000000000000000aa"
DOCKED = proto.to_b64(proto.f_varint(2, 3))
CLEANING = proto.to_b64(proto.f_bytes(1, proto.f_varint(1, 1)) + proto.f_varint(2, 5))
TUYA_DEVICE = {
    "devId": SN, "name": "Robot", "productId": "k8ttkrhlqtnfd8e9", "localKey": "k",
    "dps": {"153": DOCKED, "163": 87, "158": "Standard"},
}
ROOMS = proto.RoomTable(1, [proto.Room(1, "Laundry room"), proto.Room(0, "Room 0")])
RTC = {"device_password": "p", "ice_config": {}, "session": {"sessionId": "s"}}


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("custom_components.eufy_clean_rooms.coordinator.asyncio.sleep", AsyncMock()):
        yield


@pytest.fixture(autouse=True)
def no_real_dock_discovery():
    """Fail any test that reaches the real UDP dock discovery.

    On Linux CI the HA test plugin blocks sockets; on Windows tests/conftest.py
    lifts that guard, so without this a leak only shows up in CI.
    """
    with patch("custom_components.eufy_clean_rooms.rooms_p2p._listen_for_dock", return_value=None) as listen:
        yield
    assert not listen.called, "test reached real LAN dock discovery; use the p2p fixture"


@pytest.fixture
def cloud():
    with patch("custom_components.eufy_clean_rooms.coordinator.EufyCloud") as cls, \
         patch("custom_components.eufy_clean_rooms.config_flow.EufyCloud", cls):
        inst = cls.return_value
        inst.openudid = "udid"
        inst.user_id = "uid-1"
        inst.has_user_center = False
        inst.login = AsyncMock()
        inst.get_mqtt_devices = AsyncMock(side_effect=RuntimeError("no user center"))
        inst.get_account_devices = AsyncMock(return_value=[])
        inst.get_device = AsyncMock(return_value=None)
        inst.get_mqtt_credentials = AsyncMock(side_effect=RuntimeError("no user center"))
        yield inst


@pytest.fixture
def tuya():
    inst = MagicMock()
    inst.region = "US"
    inst.get_devices = AsyncMock(return_value=[TUYA_DEVICE])
    inst.get_dps = AsyncMock(return_value=dict(TUYA_DEVICE["dps"]))
    inst.send = AsyncMock()
    inst.get_rtc_config = AsyncMock(return_value=RTC)
    login = AsyncMock(return_value=inst)
    with patch("custom_components.eufy_clean_rooms.coordinator.async_tuya_login", login), \
         patch("custom_components.eufy_clean_rooms.config_flow.async_tuya_login", login):
        inst.login = login
        yield inst


@pytest.fixture
def p2p():
    with patch("custom_components.eufy_clean_rooms.coordinator.find_dock_host", AsyncMock(return_value="192.0.2.5")) as find, \
         patch("custom_components.eufy_clean_rooms.coordinator.probe_dock", AsyncMock()) as probe, \
         patch("custom_components.eufy_clean_rooms.coordinator.fetch_rooms", AsyncMock(return_value=ROOMS)) as fetch:
        yield MagicMock(find=find, probe=probe, fetch=fetch)


def make_entry(**extra) -> MockConfigEntry:
    data = {
        CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw", "openudid": "udid", "device_sn": SN,
        "device_model": "T2080A", "device_name": "Robot", "transport": "tuya", "tuya_region": "US",
    }
    data.update(extra)
    return MockConfigEntry(domain=DOMAIN, unique_id=SN, title="Robot", data=data)


async def setup(hass: HomeAssistant, entry: MockConfigEntry | None = None) -> MockConfigEntry:
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if task := entry.runtime_data.rooms_task:  # the P2P room fetch runs in the background
        await task
        await hass.async_block_till_done()
    return entry


def seed_store(
    hass_storage, rooms: list[tuple[int, str]], fetched_at: float, map_id: int = 1, source: str | None = "p2p"
) -> None:
    data = {"map_id": map_id, "rooms": [{"id": i, "name": n} for i, n in rooms], "fetched_at": fetched_at}
    if source:
        data["source"] = source
    hass_storage[f"{DOMAIN}.{SN}"] = {"version": 1, "key": f"{DOMAIN}.{SN}", "data": data}


def issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"dock_unreachable_{SN}")


async def test_tuya_setup_fetches_rooms_over_p2p(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    await setup(hass)
    state = hass.states.get("vacuum.robot")
    assert state.state == "docked" and state.attributes["map_id"] == 1
    assert [r["name"] for r in state.attributes["rooms"]] == ["Laundry room", "Room 0"]
    assert hass.states.get("button.robot_vacuum_laundry_room")
    assert hass.states.get("button.robot_mop_room_0")
    assert tuya.get_rtc_config.await_count == 1
    p2p.probe.assert_awaited_once_with("192.0.2.5")
    assert p2p.fetch.await_args.kwargs["local_key"] == "k"
    assert issue(hass) is None


async def test_tuya_room_clean_uses_local_map_and_zero_order(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    await setup(hass)
    await hass.services.async_call("button", "press", {"entity_id": "button.robot_vacuum_laundry_room"}, blocking=True)
    assert [c.args for c in tuya.send.await_args_list] == [
        (SN, {"154": proto.clean_param_request(clean_type="vacuum")}),
        (SN, {"152": proto.select_rooms_clean([1], 1, zero_based_order=True)}),
    ]


async def test_tuya_poll_updates_state(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    await setup(hass)
    tuya.get_dps.return_value = {"153": CLEANING}
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get("vacuum.robot").state == "cleaning"
    assert tuya.get_dps.await_count >= 1


async def test_busy_dock_never_fetches_rtc(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    p2p.probe.side_effect = DockBusy("refused")
    await setup(hass)
    assert p2p.probe.await_count == 6
    assert tuya.get_rtc_config.await_count == 0
    assert issue(hass) is None


async def test_unreachable_dock_raises_issue_keeps_rooms(hass: HomeAssistant, cloud, tuya, p2p, hass_storage) -> None:
    seed_store(hass_storage, [(2, "Kitchen")], fetched_at=0)
    p2p.probe.side_effect = DockUnreachable("no route")
    await setup(hass)
    assert issue(hass) is not None
    assert [r["name"] for r in hass.states.get("vacuum.robot").attributes["rooms"]] == ["Kitchen"]
    assert tuya.get_rtc_config.await_count == 0


async def test_fresh_store_skips_p2p(hass: HomeAssistant, cloud, tuya, p2p, hass_storage) -> None:
    seed_store(hass_storage, [(2, "Kitchen")], fetched_at=time.time())
    await setup(hass)
    assert p2p.fetch.await_count == 0
    assert hass.states.get("button.robot_vacuum_kitchen")


async def test_refresh_rooms_service_forces_p2p(hass: HomeAssistant, cloud, tuya, p2p, hass_storage) -> None:
    seed_store(hass_storage, [(2, "Kitchen")], fetched_at=time.time())
    entry = await setup(hass)
    await hass.services.async_call(DOMAIN, "refresh_rooms", {"entity_id": "vacuum.robot"}, blocking=True)
    await entry.runtime_data.rooms_task
    await hass.async_block_till_done()
    assert p2p.fetch.await_count == 1
    assert hass.states.get("button.robot_vacuum_laundry_room")


async def test_region_persisted(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    entry = await setup(hass, make_entry(tuya_region=None))
    assert entry.data["tuya_region"] == "US"
    assert tuya.login.await_args.kwargs.get("regions") in (None, ("EU", "US"))


async def test_dock_host_from_options_skips_discovery(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    await setup(hass, make_entry(dock_host="192.0.2.9"))
    assert p2p.find.await_count == 0
    p2p.probe.assert_awaited_once_with("192.0.2.9")


async def test_tuya_unload(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)


# ---------------------------------------------------------------- config flow + options

from homeassistant import config_entries  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

from custom_components.eufy_clean_rooms.tuya import TuyaError  # noqa: E402

MQTT_DEVICE = {"device_sn": "T2320XYZ", "device_model": "T2320", "device_name": "Upstairs", "dps": {}}


async def _start_flow(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw"})


async def test_config_flow_lists_tuya_robot(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    result = await _start_flow(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Robot"
    data = result["data"]
    assert data["transport"] == "tuya" and data["device_sn"] == SN
    assert data["device_model"] == "T2080A" and data["tuya_region"] == "US"
    # Creating the entry sets it up, and setup starts the room fetch in the background.
    await hass.async_block_till_done()
    if task := result["result"].runtime_data.rooms_task:
        await task
    assert p2p.find.await_count == 1


async def test_config_flow_mixed_account_offers_both(hass: HomeAssistant, cloud, tuya) -> None:
    cloud.has_user_center = True
    cloud.get_mqtt_devices = AsyncMock(return_value=[MQTT_DEVICE])
    result = await _start_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "device"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device_sn": "T2320XYZ"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["transport"] == "mqtt" and result["data"]["device_model"] == "T2320"


async def test_config_flow_tuya_login_failure_still_offers_mqtt(hass: HomeAssistant, cloud, tuya) -> None:
    cloud.has_user_center = True
    cloud.get_mqtt_devices = AsyncMock(return_value=[MQTT_DEVICE])
    tuya.login.side_effect = TuyaError("USER_NOT_EXISTS", "no")
    result = await _start_flow(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["transport"] == "mqtt"


async def test_config_flow_no_robots_anywhere(hass: HomeAssistant, cloud, tuya) -> None:
    tuya.get_devices.return_value = []
    result = await _start_flow(hass)
    assert result["errors"] == {"base": "no_devices"}


async def test_options_flow_sets_dock_host(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"dock_host": "192.0.2.9"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.data["dock_host"] == "192.0.2.9"
    if task := entry.runtime_data.rooms_task:
        await task
    assert p2p.probe.await_args.args == ("192.0.2.9",)


async def test_options_flow_aborts_for_mqtt_entry(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    entry = make_entry(transport="mqtt")
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_tuya"


# ---------------------------------------------------------------- review fixes (liveness and recovery)

import asyncio  # noqa: E402

from custom_components.eufy_clean_rooms.tuya import TuyaSessionError  # noqa: E402


async def test_tuya_unavailable_after_failed_polls_then_recovers(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    await setup(hass)
    err = TuyaSessionError("USER_SESSION_LOSS", "gone")
    tuya.get_dps.side_effect = [err, err]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get("vacuum.robot").state == "unavailable"
    assert hass.states.get("sensor.robot_battery").state == "unavailable"
    tuya.get_dps.side_effect = None
    tuya.get_dps.return_value = {"153": DOCKED}
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert hass.states.get("vacuum.robot").state == "docked"


async def test_stale_cached_host_is_rediscovered(hass: HomeAssistant, cloud, tuya, p2p, hass_storage) -> None:
    seed_store(hass_storage, [(2, "Kitchen")], fetched_at=0)
    hass_storage[f"{DOMAIN}.{SN}"]["data"]["dock_host"] = "192.0.2.5"
    p2p.probe.side_effect = [DockUnreachable("no route"), None]
    p2p.find.return_value = "192.0.2.7"
    await setup(hass)
    assert [c.args for c in p2p.probe.await_args_list] == [("192.0.2.5",), ("192.0.2.7",)]
    assert p2p.fetch.await_args.kwargs["host"] == "192.0.2.7"
    assert issue(hass) is None


async def test_refresh_service_returns_before_fetch_finishes(hass: HomeAssistant, cloud, tuya, p2p, hass_storage) -> None:
    seed_store(hass_storage, [(2, "Kitchen")], fetched_at=time.time())
    entry = await setup(hass)
    gate = asyncio.Event()

    async def slow_fetch(**kwargs):
        await gate.wait()
        return ROOMS

    p2p.fetch.side_effect = slow_fetch
    await asyncio.wait_for(
        hass.services.async_call(DOMAIN, "refresh_rooms", {"entity_id": "vacuum.robot"}, blocking=True), 1
    )
    assert entry.runtime_data.rooms_task is not None and not entry.runtime_data.rooms_task.done()
    gate.set()
    await entry.runtime_data.rooms_task
    await hass.async_block_till_done()
    assert hass.states.get("button.robot_vacuum_laundry_room")


async def test_unreachable_dock_retries_after_backoff(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    p2p.probe.side_effect = [DockUnreachable("no route"), DockUnreachable("no route"), None]
    p2p.find.return_value = "192.0.2.5"
    entry = await setup(hass)
    assert p2p.probe.await_count == 1 and issue(hass) is not None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()
    await entry.runtime_data.rooms_task
    assert p2p.probe.await_count == 2
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301 + 1801))
    await hass.async_block_till_done()
    await entry.runtime_data.rooms_task
    await hass.async_block_till_done()
    assert p2p.probe.await_count == 3
    assert issue(hass) is None
    assert hass.states.get("button.robot_vacuum_laundry_room")


async def test_no_rooms_error_mentions_refresh_on_tuya(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    p2p.probe.side_effect = DockUnreachable("no route")
    entry = await setup(hass)
    with pytest.raises(HomeAssistantError, match="Refresh rooms"):
        await entry.runtime_data.async_clean_rooms([1])


async def test_config_flow_tuya_crash_still_offers_mqtt(hass: HomeAssistant, cloud, tuya) -> None:
    cloud.has_user_center = True
    cloud.get_mqtt_devices = AsyncMock(return_value=[MQTT_DEVICE])
    tuya.login.side_effect = RuntimeError("unexpected shape")
    result = await _start_flow(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"]["transport"] == "mqtt"


async def test_stored_region_failure_falls_back_to_all_regions(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    tuya.login.side_effect = [TuyaError("USER_NOT_EXISTS", "no"), tuya]
    entry = await setup(hass)
    assert [c.args[2] for c in tuya.login.await_args_list] == [("US",), ("EU", "US")]
    assert entry.data["tuya_region"] == "US"


# The cloud's DPS 165 on an S1 Pro: one placeholder room on the cloud map id.
CLOUD_ROOMS_165 = proto.to_b64(
    proto.f_bytes(1, proto.f_varint(1, 2178) + proto.f_bytes(2, proto.f_varint(1, 0)))
)


async def test_tuya_ignores_cloud_room_snapshot(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    """On Tuya robots the cloud's DPS 165 is not the local room table; only P2P counts."""
    device = {**TUYA_DEVICE, "dps": {**TUYA_DEVICE["dps"], "165": CLOUD_ROOMS_165}}
    tuya.get_devices.return_value = [device]
    tuya.get_dps.return_value = dict(device["dps"])
    await setup(hass)
    assert p2p.fetch.await_count == 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    state = hass.states.get("vacuum.robot")
    assert state.attributes["map_id"] == 1
    assert [r["name"] for r in state.attributes["rooms"]] == ["Laundry room", "Room 0"]


async def test_tuya_refetches_rooms_not_from_p2p(hass: HomeAssistant, cloud, tuya, p2p, hass_storage) -> None:
    """A fresh saved list that didn't come from P2P (0.2.0/0.2.1 cloud snapshot) is replaced."""
    seed_store(hass_storage, [(0, "Room 0")], fetched_at=time.time(), map_id=2178, source=None)
    await setup(hass)
    assert p2p.fetch.await_count == 1
    assert hass.states.get("vacuum.robot").attributes["map_id"] == 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))  # delayed store write
    await hass.async_block_till_done()
    assert hass_storage[f"{DOMAIN}.{SN}"]["data"]["source"] == "p2p"


async def test_dock_found_by_lan_scan_when_broadcasts_unheard(hass: HomeAssistant, cloud, tuya, p2p) -> None:
    """Another Tuya integration owning UDP 6667 means no broadcast; scan HA's subnet instead."""
    p2p.find.return_value = None
    adapters = [
        {"enabled": True, "ipv4": [{"address": "192.0.2.10", "network_prefix": 24}]},
        {"enabled": True, "ipv4": [{"address": "10.0.0.2", "network_prefix": 8}]},  # too big to scan
        {"enabled": False, "ipv4": [{"address": "198.51.100.2", "network_prefix": 24}]},
    ]
    with patch("custom_components.eufy_clean_rooms.coordinator.network.async_get_adapters", AsyncMock(return_value=adapters)), \
         patch("custom_components.eufy_clean_rooms.coordinator.scan_for_dock", AsyncMock(return_value="192.0.2.42")) as scan:
        await setup(hass)
    hosts, device_id, local_key = scan.await_args.args
    assert len(hosts) == 253 and "192.0.2.10" not in hosts and "192.0.2.42" in hosts
    assert (device_id, local_key) == (SN, "k")
    p2p.probe.assert_awaited_once_with("192.0.2.42")
    assert hass.states.get("button.robot_vacuum_laundry_room")
    assert issue(hass) is None
