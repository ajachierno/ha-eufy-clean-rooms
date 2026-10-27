"""End-to-end tests for a Tuya-cloud robot, with the Tuya cloud and the P2P channel mocked."""

from __future__ import annotations

from datetime import timedelta
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
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


def seed_store(hass_storage, rooms: list[tuple[int, str]], fetched_at: float) -> None:
    hass_storage[f"{DOMAIN}.{SN}"] = {
        "version": 1, "key": f"{DOMAIN}.{SN}",
        "data": {"map_id": 1, "rooms": [{"id": i, "name": n} for i, n in rooms], "fetched_at": fetched_at},
    }


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
    await setup(hass)
    await hass.services.async_call(DOMAIN, "refresh_rooms", {"entity_id": "vacuum.robot"}, blocking=True)
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
