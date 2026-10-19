"""End-to-end tests against real Home Assistant, with the Eufy cloud mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eufy_clean_rooms import proto
from custom_components.eufy_clean_rooms.api import EufyAuthError
from custom_components.eufy_clean_rooms.const import DOMAIN

SN = "T2080A1234567890"


def room_dps(rooms: list[tuple[int, str]], map_id: int = 3) -> str:
    table = proto.f_varint(1, map_id) + b"".join(
        proto.f_bytes(2, proto.f_varint(1, rid) + proto.f_bytes(2, name.encode())) for rid, name in rooms
    )
    return proto.to_b64(proto.f_bytes(1, table))


DOCKED = proto.to_b64(proto.f_varint(2, 3))
DEVICE = {
    "device_sn": SN,
    "device_model": "T2080A",
    "device_name": "Downstairs Robot",
    "dps": {"153": DOCKED, "163": 87, "158": "Standard", "165": room_dps([(1, "Living Room"), (2, "Kitchen")])},
}
MQTT_CREDS = {
    "user_id": "u1", "app_name": "eufy_home", "thing_name": "t", "endpoint_addr": "x.iot",
    "certificate_pem": "C", "private_key": "K",
}


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
        inst.login = AsyncMock()
        inst.get_mqtt_devices = AsyncMock(return_value=[DEVICE])
        inst.get_account_devices = AsyncMock(return_value=[])
        inst.get_device = AsyncMock(return_value=DEVICE)
        inst.get_mqtt_credentials = AsyncMock(return_value=MQTT_CREDS)
        yield inst


@pytest.fixture
def mqtt():
    with patch("custom_components.eufy_clean_rooms.coordinator.EufyMqtt") as cls:
        inst = MagicMock()
        inst.user_id = "u1"
        inst.send = AsyncMock()
        inst.disconnect = AsyncMock()

        def make(creds, udid, sn, model, on_dps, on_conn):
            inst.on_dps, inst.on_conn = on_dps, on_conn

            async def connect():
                on_conn(True)

            inst.connect = connect
            return inst

        cls.side_effect = make
        yield inst


def make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, unique_id=SN, title="Downstairs Robot",
        data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw", "openudid": "udid",
              "device_sn": SN, "device_model": "T2080A", "device_name": "Downstairs Robot"},
    )


async def setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def sent(mqtt) -> list[dict]:
    return [c.args[0] for c in mqtt.send.await_args_list]


async def test_config_flow(hass: HomeAssistant, cloud, mqtt) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Downstairs Robot"
    assert result["data"]["device_sn"] == SN and result["data"]["device_model"] == "T2080A"


async def test_config_flow_bad_password(hass: HomeAssistant, cloud) -> None:
    cloud.login.side_effect = EufyAuthError("nope")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_config_flow_no_mqtt_devices(hass: HomeAssistant, cloud) -> None:
    cloud.get_mqtt_devices.return_value = []
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {"base": "no_devices"}


async def test_setup_entities_and_state(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    state = hass.states.get("vacuum.downstairs_robot")
    assert state.state == "docked"
    assert state.attributes["fan_speed"] == "Standard"
    assert [r["name"] for r in state.attributes["rooms"]] == ["Living Room", "Kitchen"]
    assert hass.states.get("sensor.downstairs_robot_battery").state == "87"
    assert hass.states.get("button.downstairs_robot_vacuum_living_room")
    assert hass.states.get("button.downstairs_robot_mop_kitchen")


async def test_button_press_sends_room_clean(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.downstairs_robot_mop_kitchen"}, blocking=True
    )
    assert sent(mqtt) == [
        {"154": proto.clean_param_request(clean_type="mop")},
        {"152": proto.select_rooms_clean([2], map_id=3)},
    ]


async def test_clean_rooms_service_by_name(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    await hass.services.async_call(
        DOMAIN, "clean_rooms",
        {"entity_id": "vacuum.downstairs_robot", "rooms": ["kitchen", "Living Room"], "fan_speed": "Max"},
        blocking=True,
    )
    # S1 Pro reports suction as a word on DPS 158, so it is set there, not in CleanParam
    assert sent(mqtt) == [{"158": "Max"}, {"152": proto.select_rooms_clean([2, 1], map_id=3)}]
    with pytest.raises(HomeAssistantError, match="Unknown room"):
        await hass.services.async_call(
            DOMAIN, "clean_rooms", {"entity_id": "vacuum.downstairs_robot", "rooms": ["Garage"]}, blocking=True
        )


async def test_send_command_room_clean(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    await hass.services.async_call(
        "vacuum", "send_command",
        {"entity_id": "vacuum.downstairs_robot", "command": "room_clean", "params": {"rooms": ["Living Room"]}},
        blocking=True,
    )
    assert sent(mqtt) == [{"152": proto.select_rooms_clean([1], map_id=3)}]


async def test_basic_controls(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    for service, method in [("start", proto.START_AUTO_CLEAN), ("pause", proto.PAUSE_TASK),
                            ("return_to_base", proto.START_GOHOME), ("stop", proto.STOP_TASK)]:
        mqtt.send.reset_mock()
        await hass.services.async_call("vacuum", service, {"entity_id": "vacuum.downstairs_robot"}, blocking=True)
        assert sent(mqtt) == [{"152": proto.mode_ctrl(method)}], service


async def test_room_list_updates_live(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    mqtt.on_dps({"165": room_dps([(1, "Living Room"), (4, "Office")], map_id=5)})
    await hass.async_block_till_done()
    reg = er.async_get(hass)
    assert hass.states.get("button.downstairs_robot_vacuum_office")
    assert reg.async_get("button.downstairs_robot_vacuum_kitchen") is None
    assert hass.states.get("vacuum.downstairs_robot").attributes["map_id"] == 5


async def test_clean_area_segments(hass: HomeAssistant, cloud, mqtt, hass_ws_client) -> None:
    await setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "vacuum/get_segments", "entity_id": "vacuum.downstairs_robot"})
    msg = await client.receive_json()
    assert msg["success"], msg
    assert [s["name"] for s in msg["result"]["segments"]] == ["Living Room", "Kitchen"]


async def test_cleaning_status_while_running(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    running = proto.to_b64(proto.f_bytes(1, proto.f_varint(1, 1)) + proto.f_varint(2, 5))
    mqtt.on_dps({"153": running})
    await hass.async_block_till_done()
    assert hass.states.get("vacuum.downstairs_robot").state == "cleaning"
    assert hass.states.get("sensor.downstairs_robot_task").state == "room cleaning"


async def test_mqtt_drop_makes_unavailable(hass: HomeAssistant, cloud, mqtt) -> None:
    await setup(hass)
    mqtt.on_conn(False)
    await hass.async_block_till_done()
    assert hass.states.get("vacuum.downstairs_robot").state == "unavailable"


async def test_unload(hass: HomeAssistant, cloud, mqtt) -> None:
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    mqtt.disconnect.assert_awaited()
